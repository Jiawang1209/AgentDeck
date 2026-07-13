"""Pure one-transition Mission scheduler decisions.

This module owns no clocks, persistence, transport, or retry loop.  Callers
must assemble one complete authoritative fact snapshot, then explicitly apply
the single returned decision.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Generic, Literal, Mapping, Protocol, TypeVar

from ..mission import MISSION_STATUSES

MISSION_STATES = frozenset(("idle", *MISSION_STATUSES))
STEP_STATES = frozenset({"none", "pending", "active", "completed", "failed", "blocked"})
ATTEMPT_STATES = frozenset(
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
REPLY_STATES = frozenset({"none", "pending", "received", "validated", "invalid"})
HANDOFF_STATES = frozenset({"none", "pending", "recorded"})
PERMISSION_STATES = frozenset({"none", "pending", "approved", "denied", "expired"})
SNAPSHOT_STATES = frozenset({"valid", "missing", "drift"})
LINEAGE_STATES = frozenset({"valid", "missing"})
OWNERSHIP_STATES = frozenset({"owned", "conflict"})
DECISION_KINDS = frozenset(
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

_FACT_FIELDS = (
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
)
_MISSION_ID = re.compile(r"mis_[0-9a-f]{12}")
_STEP_ID = re.compile(r"step_[1-9][0-9]*")
_ATTEMPT_ID = re.compile(r"mat_[0-9a-f]{12}")
_ACTIVE_ATTEMPT_STATES = frozenset({"prepared", "submitted", "running"})
_SUCCESSFUL_ATTEMPT_STATES = frozenset({"completed", "succeeded"})
_TERMINAL_MISSION_STATES = frozenset(
    {"completed", "stopped", "interrupted"}
)


class SchedulerError(ValueError):
    """Sanitized invalid fact or decision error."""


MissionState = Literal[
    "idle",
    "pending_confirmation",
    "preparing",
    "running",
    "completed",
    "stopped",
    "interrupted",
]
StepState = Literal["none", "pending", "active", "completed", "failed", "blocked"]
AttemptState = Literal[
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
]
ReplyState = Literal["none", "pending", "received", "validated", "invalid"]
HandoffState = Literal["none", "pending", "recorded"]
PermissionState = Literal["none", "pending", "approved", "denied", "expired"]
SnapshotState = Literal["valid", "missing", "drift"]
LineageState = Literal["valid", "missing"]
OwnershipState = Literal["owned", "conflict"]
DecisionKind = Literal[
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
]


def _optional_identity(value: object, pattern: re.Pattern[str], label: str) -> None:
    if value is not None and (type(value) is not str or pattern.fullmatch(value) is None):
        raise SchedulerError(f"invalid scheduler facts: {label} identity")


@dataclass(frozen=True)
class SchedulerFacts:
    mission_id: str | None
    mission_state: MissionState
    step_id: str | None
    step_state: StepState
    attempt_id: str | None
    attempt_state: AttemptState
    reply_state: ReplyState
    handoff_state: HandoffState
    permission_state: PermissionState
    worker_ready: bool
    next_step_eligible: bool
    all_steps_completed: bool
    snapshot_state: SnapshotState
    lineage_state: LineageState
    ownership_state: OwnershipState
    active_attempt_count: int
    blocker: str | None

    def __post_init__(self) -> None:
        state_fields = (
            (self.mission_state, MISSION_STATES),
            (self.step_state, STEP_STATES),
            (self.attempt_state, ATTEMPT_STATES),
            (self.reply_state, REPLY_STATES),
            (self.handoff_state, HANDOFF_STATES),
            (self.permission_state, PERMISSION_STATES),
            (self.snapshot_state, SNAPSHOT_STATES),
            (self.lineage_state, LINEAGE_STATES),
            (self.ownership_state, OWNERSHIP_STATES),
        )
        if any(type(value) is not str or value not in allowed for value, allowed in state_fields):
            raise SchedulerError("invalid scheduler facts: unknown state")
        if any(
            type(value) is not bool
            for value in (
                self.worker_ready,
                self.next_step_eligible,
                self.all_steps_completed,
            )
        ):
            raise SchedulerError("invalid scheduler facts: boolean field")
        if type(self.active_attempt_count) is not int or self.active_attempt_count < 0:
            raise SchedulerError("invalid scheduler facts: active attempt count")
        if self.blocker is not None and (
            type(self.blocker) is not str or not self.blocker.strip()
        ):
            raise SchedulerError("invalid scheduler facts: blocker")

        _optional_identity(self.mission_id, _MISSION_ID, "mission")
        _optional_identity(self.step_id, _STEP_ID, "step")
        _optional_identity(self.attempt_id, _ATTEMPT_ID, "attempt")
        if self.mission_state == "idle":
            if (
                self.mission_id is not None
                or self.step_id is not None
                or self.attempt_id is not None
                or self.step_state != "none"
                or self.attempt_state != "none"
                or self.reply_state != "none"
                or self.handoff_state != "none"
                or self.permission_state != "none"
                or self.next_step_eligible
                or self.all_steps_completed
                or self.snapshot_state == "drift"
                or self.lineage_state != "valid"
                or self.ownership_state != "owned"
                or self.active_attempt_count != 0
                or self.blocker is not None
            ):
                raise SchedulerError("invalid scheduler facts: idle Mission facts")
            return
        if self.mission_id is None:
            raise SchedulerError("invalid scheduler facts: mission identity")
        if self.mission_state == "pending_confirmation" and (
            self.step_id is not None
            or self.step_state != "none"
            or self.attempt_id is not None
            or self.attempt_state != "none"
            or self.active_attempt_count != 0
        ):
            raise SchedulerError("invalid scheduler facts: unconfirmed Mission lineage")
        if self.step_state != "none" and self.step_id is None:
            raise SchedulerError("invalid scheduler facts: step identity")
        if self.step_id is None and self.attempt_id is not None:
            raise SchedulerError("invalid scheduler facts: step identity")
        if self.attempt_state in _ACTIVE_ATTEMPT_STATES:
            if self.active_attempt_count < 1:
                raise SchedulerError("invalid scheduler facts: active attempt count")
        elif self.active_attempt_count == 1:
            raise SchedulerError("invalid scheduler facts: active attempt count")
        if self.attempt_state != "none" and self.attempt_id is None:
            raise SchedulerError("invalid scheduler facts: attempt identity")
        if self.attempt_id is not None and self.attempt_state == "none":
            raise SchedulerError("invalid scheduler facts: attempt identity")
        if self.reply_state != "none" and self.attempt_id is None:
            raise SchedulerError("invalid scheduler facts: reply lineage")
        if self.handoff_state != "none" and self.reply_state != "validated":
            raise SchedulerError("invalid scheduler facts: handoff lineage")
        if self.next_step_eligible and (
            self.reply_state != "validated" or self.handoff_state != "recorded"
        ):
            raise SchedulerError("invalid scheduler facts: next-step lineage")

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "SchedulerFacts":
        if type(value) is not dict or set(value) != set(_FACT_FIELDS):
            raise SchedulerError("invalid scheduler facts: exact fields required")
        return cls(**dict(value))  # type: ignore[arg-type]

    def summary(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in _FACT_FIELDS}


@dataclass(frozen=True)
class SchedulerDecision:
    kind: DecisionKind
    mission_id: str | None
    step_id: str | None
    attempt_id: str | None
    blocker: str | None

    def __post_init__(self) -> None:
        if type(self.kind) is not str or self.kind not in DECISION_KINDS:
            raise SchedulerError("invalid scheduler decision")
        for value, pattern in (
            (self.mission_id, _MISSION_ID),
            (self.step_id, _STEP_ID),
            (self.attempt_id, _ATTEMPT_ID),
        ):
            if value is not None and (type(value) is not str or pattern.fullmatch(value) is None):
                raise SchedulerError("invalid scheduler decision")
        if self.kind == "blocked":
            if type(self.blocker) is not str or not self.blocker.strip():
                raise SchedulerError("invalid scheduler decision")
        elif self.blocker is not None:
            raise SchedulerError("invalid scheduler decision")
        if self.step_id is not None and self.mission_id is None:
            raise SchedulerError("invalid scheduler decision")
        if self.attempt_id is not None and self.step_id is None:
            raise SchedulerError("invalid scheduler decision")
        if self.kind != "idle" and self.mission_id is None:
            raise SchedulerError("invalid scheduler decision")
        if self.kind == "prepare_dispatch" and (
            self.step_id is None or self.attempt_id is not None
        ):
            raise SchedulerError("invalid scheduler decision")
        if self.kind == "complete_mission" and (
            self.step_id is not None or self.attempt_id is not None
        ):
            raise SchedulerError("invalid scheduler decision")
        if self.kind in {
            "dispatch_prepared",
            "await_worker",
            "validate_reply",
            "record_handoff",
            "activate_next",
            "wait_ambiguity",
        } and self.attempt_id is None:
            raise SchedulerError("invalid scheduler decision")


def _decision(
    kind: DecisionKind,
    facts: SchedulerFacts,
    *,
    blocker: str | None = None,
) -> SchedulerDecision:
    return SchedulerDecision(
        kind,
        facts.mission_id,
        facts.step_id,
        facts.attempt_id,
        blocker,
    )


def schedule_gate(facts: SchedulerFacts) -> SchedulerDecision:
    """Select exactly one next transition from a validated fact snapshot."""
    if type(facts) is not SchedulerFacts:
        raise SchedulerError("invalid scheduler facts: SchedulerFacts required")

    if facts.mission_state == "idle":
        return _decision("idle", facts)

    # Integrity and authority always outrank wait or progress decisions.
    if facts.lineage_state != "valid":
        return _decision("blocked", facts, blocker="missing Mission lineage")
    if facts.ownership_state != "owned":
        return _decision("blocked", facts, blocker="Worker ownership conflict")
    if facts.mission_state == "pending_confirmation":
        if facts.snapshot_state == "drift":
            return _decision("blocked", facts, blocker="frozen snapshot drift")
        if facts.blocker is not None:
            return _decision("blocked", facts, blocker=facts.blocker)
        return _decision("wait_human", facts)
    if facts.snapshot_state != "valid":
        reason = (
            "frozen snapshot drift"
            if facts.snapshot_state == "drift"
            else "frozen snapshot missing"
        )
        return _decision("blocked", facts, blocker=reason)
    if facts.active_attempt_count > 1:
        return _decision("blocked", facts, blocker="conflicting active attempts")
    if facts.all_steps_completed and facts.active_attempt_count:
        return _decision(
            "blocked", facts, blocker="completion conflicts with active attempt"
        )
    if (
        facts.mission_state in _TERMINAL_MISSION_STATES
        and facts.active_attempt_count
    ):
        return _decision(
            "blocked", facts, blocker="terminal Mission conflicts with active attempt"
        )

    if facts.blocker is not None:
        return _decision("blocked", facts, blocker=facts.blocker)
    if (
        facts.attempt_state in _ACTIVE_ATTEMPT_STATES
        and (
            facts.reply_state in {"received", "validated"}
            or facts.handoff_state != "none"
            or facts.next_step_eligible
        )
    ):
        return _decision(
            "blocked",
            facts,
            blocker="Worker reply precedes successful terminal attempt",
        )
    if (
        facts.mission_state in _TERMINAL_MISSION_STATES
        and facts.permission_state == "pending"
    ):
        return _decision(
            "blocked", facts, blocker="terminal Mission has pending permission"
        )
    if (
        facts.mission_state in _TERMINAL_MISSION_STATES
        and facts.attempt_state == "ambiguous"
    ):
        return _decision(
            "blocked", facts, blocker="terminal Mission retains ambiguous attempt"
        )
    if facts.permission_state in {"denied", "expired"}:
        reason = (
            "permission denied"
            if facts.permission_state == "denied"
            else "permission expired"
        )
        return _decision("blocked", facts, blocker=reason)
    if facts.step_state in {"failed", "blocked"}:
        return _decision(
            "blocked",
            facts,
            blocker=facts.blocker or f"Mission step is {facts.step_state}",
        )
    if facts.attempt_state in {"failed", "cancelled", "interrupted"}:
        return _decision(
            "blocked",
            facts,
            blocker=facts.blocker or f"Mission attempt {facts.attempt_state}",
        )
    if facts.reply_state == "invalid":
        return _decision("blocked", facts, blocker=facts.blocker or "Worker reply is invalid")
    if facts.mission_state == "completed" and (
        facts.step_id is not None or facts.attempt_id is not None
    ):
        return _decision(
            "blocked", facts, blocker="terminal Mission retains current lineage"
        )
    if facts.mission_state in _TERMINAL_MISSION_STATES:
        return _decision("idle", facts)
    if facts.permission_state == "pending":
        return _decision("wait_human", facts)
    if facts.attempt_state == "ambiguous":
        return _decision("wait_ambiguity", facts)

    if facts.all_steps_completed:
        if facts.attempt_id is not None:
            if (
                facts.attempt_state in _SUCCESSFUL_ATTEMPT_STATES
                and facts.reply_state != "validated"
            ):
                return _decision(
                    "blocked",
                    facts,
                    blocker="completion has unverified Worker result",
                )
            if (
                facts.attempt_state in _SUCCESSFUL_ATTEMPT_STATES
                and facts.handoff_state != "recorded"
            ):
                return _decision(
                    "blocked",
                    facts,
                    blocker="completion has unrecorded handoff",
                )
            return _decision(
                "blocked", facts, blocker="completion conflicts with current step"
            )
        if facts.step_id is not None:
            return _decision(
                "blocked", facts, blocker="completion conflicts with current step"
            )
        return _decision("complete_mission", facts)
    if (
        facts.step_state == "pending"
        and facts.attempt_state == "none"
        and facts.worker_ready
    ):
        return _decision("prepare_dispatch", facts)
    if facts.step_state == "pending" and facts.attempt_state == "none":
        return _decision("blocked", facts, blocker="Worker is not ready")
    if facts.attempt_state == "prepared":
        if not facts.worker_ready:
            return _decision(
                "blocked", facts, blocker="Worker is not ready at dispatch"
            )
        return _decision("dispatch_prepared", facts)
    if (
        facts.attempt_state in _SUCCESSFUL_ATTEMPT_STATES
        and facts.reply_state == "received"
    ):
        return _decision("validate_reply", facts)
    if (
        facts.attempt_state in _SUCCESSFUL_ATTEMPT_STATES
        and facts.reply_state == "validated"
        and facts.handoff_state != "recorded"
    ):
        return _decision("record_handoff", facts)
    if (
        facts.attempt_state in _SUCCESSFUL_ATTEMPT_STATES
        and facts.reply_state == "validated"
        and facts.handoff_state == "recorded"
        and facts.next_step_eligible
    ):
        return _decision("activate_next", facts)
    if facts.attempt_state in {"submitted", "running"}:
        return _decision("await_worker", facts)
    if (
        facts.attempt_state in _SUCCESSFUL_ATTEMPT_STATES
        and facts.reply_state in {"none", "pending"}
    ):
        return _decision("blocked", facts, blocker="Worker result is missing")
    if facts.step_state == "active" and facts.attempt_state == "none":
        return _decision(
            "blocked", facts, blocker="active Mission step has no attempt"
        )
    if (
        facts.attempt_state in _SUCCESSFUL_ATTEMPT_STATES
        and facts.reply_state == "validated"
        and facts.handoff_state == "recorded"
    ):
        return _decision(
            "blocked", facts, blocker="successful attempt has no next transition"
        )
    return _decision("blocked", facts, blocker="incomplete active Mission facts")


EffectResult = TypeVar("EffectResult")


class SchedulerEffects(Protocol, Generic[EffectResult]):
    def apply(self, decision: SchedulerDecision) -> EffectResult: ...


def run_scheduler_once(
    facts: SchedulerFacts,
    effects: SchedulerEffects[EffectResult],
) -> EffectResult:
    """Apply one selected decision exactly once; exceptions propagate."""
    return effects.apply(schedule_gate(facts))

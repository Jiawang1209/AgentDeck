"""Closed codecs and deterministic identities for takeover ownership cycles."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType

from agentdeck.kernel.events import DomainEvent
from agentdeck.kernel.mission import ConfirmedMissionVersion, TaskDefinition
from agentdeck.kernel.permissions import Effect, PermissionProfile, PermissionScope
from agentdeck.ports.observer import ObserverCursor
from agentdeck.ports.project_evidence import ProjectEvidence


OWNERSHIP_STATES = frozenset({"automatic", "human", "returned", "interrupted"})
_OWNERSHIP_FIELDS = frozenset({
    "attempt_id", "generation", "cycle_id", "state", "baseline",
    "takeover_command_id", "return_command_id",
})
_BASELINE_FIELDS = frozenset({
    "project_evidence", "permission", "cursor", "human_attempt",
})
_PROJECT_FIELDS = frozenset(ProjectEvidence.__dataclass_fields__)
_CURSOR_FIELDS = frozenset(ObserverCursor.__dataclass_fields__)
_ATTEMPT_FIELDS = frozenset({
    "attempt_id", "task_id", "agent_instance_id", "ordinal", "state", "reason",
    "result_summary", "retryable", "acp_session_id", "effect_observed",
})


class TakeoverSourceFailure(RuntimeError):
    pass


def takeover_identity(value: object, prefix: str) -> bool:
    if type(value) is not str:
        return False
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        return False
    return bool(
        value.startswith(prefix) and value.removeprefix(prefix)
        and len(encoded) <= 255
        and not any(character.isspace() for character in value)
    )


@dataclass(frozen=True)
class TakeoverBaseline:
    project_evidence: ProjectEvidence
    permission: PermissionScope
    cursor: ObserverCursor
    human_attempt: MappingProxyType

    def __post_init__(self) -> None:
        if (
            type(self.project_evidence) is not ProjectEvidence
            or type(self.permission) is not PermissionScope
            or type(self.cursor) is not ObserverCursor
            or type(self.human_attempt) is not MappingProxyType
            or set(self.human_attempt) != _ATTEMPT_FIELDS
            or self.human_attempt["attempt_id"] != self.cursor.attempt_id
            or self.project_evidence.project_id != self.cursor.project_id
        ):
            raise ValueError("takeover baseline is invalid")


@dataclass(frozen=True)
class TakeoverOwnership:
    attempt_id: str
    generation: int
    cycle_id: str
    state: str
    baseline: TakeoverBaseline
    takeover_command_id: str
    return_command_id: str

    def __post_init__(self) -> None:
        expected_cycle = cycle_id(self.attempt_id, self.generation)
        if (
            self.state not in OWNERSHIP_STATES
            or self.cycle_id != expected_cycle
            or self.baseline.cursor.attempt_id != self.attempt_id
            or self.takeover_command_id != command_id("takeover", expected_cycle)
            or self.return_command_id != command_id("return", expected_cycle)
        ):
            raise ValueError("takeover ownership is invalid")


def cycle_id(attempt_id: str, generation: int) -> str:
    if (
        type(attempt_id) is not str or not attempt_id.startswith("att_")
        or type(generation) is not int or not 1 <= generation < 2**63
    ):
        raise ValueError("takeover cycle identity is invalid")
    digest = sha256(f"{attempt_id}\0{generation}".encode("utf-8", "strict")).hexdigest()
    return f"own_{digest[:32]}"


def command_id(operation: str, cycle: str) -> str:
    if operation not in {"takeover", "return", "interrupt"} or not cycle.startswith("own_"):
        raise ValueError("takeover command identity is invalid")
    digest = sha256(f"{operation}\0{cycle}".encode("ascii", "strict")).hexdigest()
    return f"cmd_{operation}_{digest[:32]}"


def new_ownership(
    *, attempt_id: str, generation: int, baseline: TakeoverBaseline,
) -> TakeoverOwnership:
    cycle = cycle_id(attempt_id, generation)
    return TakeoverOwnership(
        attempt_id, generation, cycle, "human", baseline,
        command_id("takeover", cycle), command_id("return", cycle),
    )


def ownership_snapshot(value: TakeoverOwnership) -> dict[str, object]:
    if type(value) is not TakeoverOwnership:
        raise TypeError("ownership snapshot requires exact authority")
    baseline = value.baseline
    return {
        "attempt_id": value.attempt_id, "generation": value.generation,
        "cycle_id": value.cycle_id, "state": value.state,
        "baseline": {
            "project_evidence": dict(baseline.project_evidence.__dict__),
            "permission": {
                "profile": baseline.permission.profile.value,
                "effects": sorted(effect.value for effect in baseline.permission.effects),
            },
            "cursor": dict(baseline.cursor.__dict__),
            "human_attempt": dict(baseline.human_attempt),
        },
        "takeover_command_id": value.takeover_command_id,
        "return_command_id": value.return_command_id,
    }


def ownership_from_snapshot(value: object) -> TakeoverOwnership:
    if type(value) is not dict or set(value) != _OWNERSHIP_FIELDS:
        raise ValueError("stored takeover ownership fields are invalid")
    baseline = value["baseline"]
    if type(baseline) is not dict or set(baseline) != _BASELINE_FIELDS:
        raise ValueError("stored takeover baseline fields are invalid")
    project, permission, cursor, attempt = (
        baseline["project_evidence"], baseline["permission"],
        baseline["cursor"], baseline["human_attempt"],
    )
    if (
        type(project) is not dict or set(project) != _PROJECT_FIELDS
        or type(permission) is not dict or set(permission) != {"profile", "effects"}
        or type(permission["effects"]) is not list
        or type(cursor) is not dict or set(cursor) != _CURSOR_FIELDS
        or type(attempt) is not dict or set(attempt) != _ATTEMPT_FIELDS
    ):
        raise ValueError("stored takeover baseline values are invalid")
    try:
        decoded = TakeoverBaseline(
            ProjectEvidence(**project),
            PermissionScope(
                PermissionProfile(permission["profile"]),
                frozenset(Effect(effect) for effect in permission["effects"]),
            ),
            ObserverCursor(**cursor), MappingProxyType(dict(attempt)),
        )
        return TakeoverOwnership(
            value["attempt_id"], value["generation"], value["cycle_id"],
            value["state"], decoded, value["takeover_command_id"],
            value["return_command_id"],
        )
    except (KeyError, TypeError, ValueError):
        raise ValueError("stored takeover ownership values are invalid") from None


def ownership_with_state(
    value: TakeoverOwnership, state: str,
) -> TakeoverOwnership:
    return TakeoverOwnership(
        value.attempt_id, value.generation, value.cycle_id, state,
        value.baseline, value.takeover_command_id, value.return_command_id,
    )


def transition_result(value: TakeoverOwnership, state: str) -> dict[str, object]:
    return {
        "accepted": True, "attempt_id": value.attempt_id,
        "generation": value.generation, "cycle_id": value.cycle_id, "state": state,
    }


def ownership_event(
    *, kind: str, owner: str, occurred_at: str, product_session_id: str,
    confirmed: ConfirmedMissionVersion, task: TaskDefinition,
    acp_session_id: str, acp_session_state: str,
    ownership: TakeoverOwnership,
) -> DomainEvent:
    baseline = ownership.baseline
    cursor = baseline.cursor
    project = baseline.project_evidence
    return DomainEvent.create(
        kind=kind, aggregate_type="attempt", aggregate_id=ownership.attempt_id,
        occurred_at=occurred_at, payload={
            "product_session_id": product_session_id,
            "mission_id": confirmed.mission_id,
            "mission_version": confirmed.version,
            "mission_content_hash": confirmed.content_hash,
            "task_id": task.task_id, "attempt_id": ownership.attempt_id,
            "agent_instance_id": task.agent_instance_id,
            "acp_session_id": acp_session_id,
            "acp_session_state": acp_session_state, "owner": owner,
            "ownership_cycle_id": ownership.cycle_id,
            "ownership_generation": ownership.generation,
            "project_evidence_provenance": project.provenance,
            "project_evidence_digest": project.digest,
            "permission_profile": baseline.permission.profile.value,
            "permission_scope": sorted(
                effect.value for effect in baseline.permission.effects
            ),
            "observer_project_id": cursor.project_id,
            "observer_session_id": cursor.session_id,
            "observer_agent_id": cursor.agent_id,
            "observer_task_id": cursor.task_id,
            "observer_attempt_id": cursor.attempt_id,
            "observer_transport": cursor.transport,
            "observer_sequence": cursor.sequence,
            "observer_event_id": cursor.event_id,
            "observer_fingerprint": cursor.fingerprint,
        },
    )


def record_interrupted_ownership(
    *, store, ownership: TakeoverOwnership,
    interrupted_attempt: dict[str, object], event: DomainEvent,
) -> TakeoverOwnership:
    if (
        type(ownership) is not TakeoverOwnership or ownership.state != "human"
        or type(interrupted_attempt) is not dict
        or interrupted_attempt.get("attempt_id") != ownership.attempt_id
        or interrupted_attempt.get("state") != "interrupted"
        or type(event) is not DomainEvent
    ):
        raise ValueError("interrupted takeover authority is invalid")
    interrupted = ownership_with_state(ownership, "interrupted")
    result = transition_result(interrupted, "interrupted")
    identity = command_id("interrupt", ownership.cycle_id)

    def commit(transaction):
        if (
            transaction.load_aggregate("takeover_ownership", ownership.attempt_id)
            != ownership_snapshot(ownership)
            or transaction.load_aggregate("attempts", ownership.attempt_id)
            != interrupted_attempt
        ):
            raise ValueError("exit interruption authority drifted")
        transaction.save_aggregate(
            "takeover_ownership", ownership.attempt_id,
            ownership_snapshot(interrupted),
        )
        transaction.append_event(event)
        return result

    try:
        durable = store.execute_once(identity, "human_takeover_interrupted", commit)
    except Exception:
        durable = store.lookup_command(identity, "human_takeover_interrupted")
    if (
        durable != result
        or store.load_aggregate("takeover_ownership", ownership.attempt_id)
        != ownership_snapshot(interrupted)
    ):
        raise ValueError("exit interruption did not become durable")
    return interrupted


__all__ = [
    "TakeoverBaseline", "TakeoverOwnership", "TakeoverSourceFailure",
    "command_id", "cycle_id",
    "new_ownership", "ownership_from_snapshot", "ownership_snapshot",
    "ownership_event", "ownership_with_state", "record_interrupted_ownership",
    "takeover_identity", "transition_result",
]

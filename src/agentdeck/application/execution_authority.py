"""Pure replay authority for execution Attempt commands and snapshots."""

from __future__ import annotations

import json

from agentdeck.kernel.execution import Attempt, AttemptState
from agentdeck.kernel.mission import ConfirmedMissionVersion, TaskDefinition


_BOUND_RESULT_FIELDS = frozenset({
    "mission_id", "mission_version", "task_id", "attempt_id", "acp_session_id",
})
_STOPPED_RESULT_FIELDS = frozenset({
    "mission_id", "mission_version", "task_id", "attempt_id", "state",
    "reason", "retryable", "acp_session_id",
})


def attempt_snapshot(
    attempt: Attempt, task: TaskDefinition, acp_session_id: str | None = None,
) -> dict[str, object]:
    return {
        "attempt_id": attempt.attempt_id,
        "task_id": attempt.task_id,
        "agent_instance_id": task.agent_instance_id,
        "ordinal": attempt.ordinal,
        "state": attempt.state.value,
        "reason": attempt.reason,
        "result_summary": attempt.result_summary,
        "retryable": attempt.retryable,
        "acp_session_id": acp_session_id,
        "effect_observed": False,
    }


def bound_command_result(
    confirmed: ConfirmedMissionVersion, task: TaskDefinition, attempt: Attempt,
    acp_session_id: str,
) -> dict[str, object]:
    return {
        "mission_id": confirmed.mission_id, "mission_version": confirmed.version,
        "task_id": task.task_id, "attempt_id": attempt.attempt_id,
        "acp_session_id": acp_session_id,
    }


def stopped_command_result(
    confirmed: ConfirmedMissionVersion, task: TaskDefinition, attempt: Attempt,
    acp_session_id: str | None,
) -> dict[str, object]:
    return {
        "mission_id": confirmed.mission_id, "mission_version": confirmed.version,
        "task_id": task.task_id, "attempt_id": attempt.attempt_id,
        "state": attempt.state.value, "reason": attempt.reason,
        "retryable": attempt.retryable, "acp_session_id": acp_session_id,
    }


def _attempt_reference(
    result: object, fields: frozenset[str], message: str,
) -> str:
    if type(result) is not dict or set(result) != fields:
        raise ValueError(message)
    identity = result["attempt_id"]
    if type(identity) is not str or not identity.startswith("att_"):
        raise ValueError(message)
    return identity


def bound_attempt_reference(result: object) -> str:
    return _attempt_reference(
        result, _BOUND_RESULT_FIELDS, "bound execution attempt is invalid"
    )


def stopped_attempt_reference(result: object) -> str:
    return _attempt_reference(
        result, _STOPPED_RESULT_FIELDS, "stopped execution attempt is invalid"
    )


def _validated_attempt(
    result: object, expected_result: dict[str, object], attempt_facts: object,
    expected_facts: dict[str, object], message: str,
) -> Attempt:
    if type(result) is not dict or type(attempt_facts) is not dict or (
        json.dumps(result, sort_keys=True)
        != json.dumps(expected_result, sort_keys=True)
        or json.dumps(attempt_facts, sort_keys=True)
        != json.dumps(expected_facts, sort_keys=True)
    ):
        raise ValueError(message)
    return Attempt(
        attempt_facts["attempt_id"], attempt_facts["task_id"],
        attempt_facts["ordinal"], AttemptState(attempt_facts["state"]),
        attempt_facts["reason"], attempt_facts["result_summary"],
        attempt_facts["retryable"],
    )


def validated_bound_attempt(
    result: object, confirmed: ConfirmedMissionVersion, task: TaskDefinition,
    expected_attempt: Attempt, expected_acp_session_id: str,
    attempt_facts: object,
) -> Attempt:
    return _validated_attempt(
        result,
        bound_command_result(
            confirmed, task, expected_attempt, expected_acp_session_id
        ),
        attempt_facts,
        attempt_snapshot(expected_attempt, task, expected_acp_session_id),
        "bound execution attempt is invalid",
    )


def validated_stopped_attempt(
    result: object, confirmed: ConfirmedMissionVersion, task: TaskDefinition,
    expected_attempt: Attempt, expected_acp_session_id: str | None,
    attempt_facts: object,
) -> Attempt:
    return _validated_attempt(
        result,
        stopped_command_result(
            confirmed, task, expected_attempt, expected_acp_session_id
        ),
        attempt_facts,
        attempt_snapshot(expected_attempt, task, expected_acp_session_id),
        "stopped execution attempt is invalid",
    )


__all__ = [
    "attempt_snapshot", "bound_attempt_reference", "bound_command_result",
    "stopped_attempt_reference", "stopped_command_result",
    "validated_bound_attempt", "validated_stopped_attempt",
]

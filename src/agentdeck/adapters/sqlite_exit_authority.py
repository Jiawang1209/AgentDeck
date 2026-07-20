"""One connection-local SQLite projection for active project exit CAS."""

from __future__ import annotations

import json
import sqlite3

from agentdeck.adapters.sqlite_session import load_session_aggregate
from agentdeck.adapters.sqlite_validation import (
    _ATTEMPT_COLUMNS,
    _attempt_fingerprint,
    _attempt_from_row,
)
from agentdeck.kernel.execution import AttemptState
from agentdeck.kernel.session import ExitAttemptSnapshot, ExitRequest
from agentdeck.ports.exit_authority import ActiveExitAuthority
from agentdeck.ports.store import _session_identity
from agentdeck.ports.worker import WorkerHandle


_PENDING_FIELDS = (
    "pending_exit_id", "pending_exit_attempt_id",
    "canonical_pending_exit_attempt_facts", "pending_exit_attempt_hash",
    "pending_exit_requested_at",
)
_ACTIVE = ("running", "awaiting_approval", "human_controlled")


def _request(session: dict[str, object]) -> ExitRequest:
    values = tuple(session[field] for field in _PENDING_FIELDS)
    if any(value is None for value in values):
        raise ValueError("active exit request is absent")
    request_id, attempt_id, canonical, attempt_hash, requested_at = values
    if type(canonical) is not str:
        raise ValueError("active exit request is malformed")
    facts = json.loads(canonical)
    if type(facts) is not dict:
        raise ValueError("active exit request is malformed")
    snapshot = ExitAttemptSnapshot(
        facts["attempt_id"], facts["task_id"], facts["agent_instance_id"],
        facts["ordinal"], AttemptState(facts["state"]), facts["acp_session_id"],
        facts["effect_observed"], facts["durable_fingerprint"],
    )
    request = ExitRequest(request_id, snapshot, attempt_hash, requested_at)
    if (
        attempt_id != snapshot.attempt_id
        or canonical != snapshot.canonical_bytes().decode("utf-8")
    ):
        raise ValueError("active exit request lineage is malformed")
    return request


def load_active_exit_authority(
    connection: sqlite3.Connection, session_id: str,
) -> ActiveExitAuthority:
    """Load one exact active authority using only the supplied connection."""

    try:
        session_id = _session_identity(session_id)
        session = load_session_aggregate(connection, session_id)
        if session is None:
            raise ValueError
        request = _request(session)
        attempt_columns = ",".join(f"a.{column}" for column in _ATTEMPT_COLUMNS)
        rows = connection.execute(
            f"""SELECT {attempt_columns},
                       t.state,t.planned_agent_instance_id,t.mission_id,
                       t.mission_version,m.state,m.session_id,m.current_version,
                       ai.instance_id,ai.session_id,ai.transport,
                       ai.acp_session_id,ai.state
                  FROM attempts a
                  JOIN tasks t ON t.task_id=a.task_id
                  JOIN missions m ON m.mission_id=t.mission_id
                  JOIN agent_instances ai ON ai.instance_id=a.agent_instance_id
                 WHERE m.session_id=?
                   AND a.state IN (?,?,?)
                 ORDER BY a.attempt_id""",
            (session_id, *_ACTIVE),
        ).fetchall()
        if len(rows) != 1:
            raise ValueError
        row = rows[0]
        attempt_row = tuple(row[:len(_ATTEMPT_COLUMNS)])
        attempt, attempt_values = _attempt_from_row(attempt_row)
        snapshot = ExitAttemptSnapshot(
            attempt.attempt_id, attempt.task_id,
            attempt_values["agent_instance_id"], attempt.ordinal,
            attempt.state, attempt_values["acp_session_id"],
            bool(attempt_values["effect_observed"]),
            _attempt_fingerprint(attempt_values),
        )
        if snapshot != request.attempt:
            raise ValueError
        facts = row[len(_ATTEMPT_COLUMNS):]
        (
            task_state, task_agent, mission_id, mission_version,
            mission_state, mission_session, mission_current_version,
            agent_id, agent_session, agent_transport, agent_acp, agent_state,
        ) = facts
        handle = WorkerHandle(
            snapshot.acp_session_id, snapshot.agent_instance_id,
            snapshot.task_id, snapshot.attempt_id,
            transport=agent_transport,
        )
        return ActiveExitAuthority(
            session_id=session_id, session_state=session["state"],
            request=request, task_id=snapshot.task_id, task_state=task_state,
            task_agent_instance_id=task_agent, task_mission_id=mission_id,
            task_mission_version=mission_version, mission_state=mission_state,
            mission_session_id=mission_session,
            mission_current_version=mission_current_version,
            agent_instance_id=agent_id, agent_session_id=agent_session,
            agent_transport=agent_transport, agent_acp_session_id=agent_acp,
            agent_state=agent_state, worker_handle=handle,
        )
    except (
        IndexError, KeyError, TypeError, ValueError, RuntimeError, UnicodeError,
        sqlite3.Error,
    ):
        raise ValueError("active exit authority is invalid") from None


__all__ = ["load_active_exit_authority"]

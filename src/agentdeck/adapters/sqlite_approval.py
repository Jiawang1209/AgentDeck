"""Canonical approval-row persistence for the SQLite adapter."""

from __future__ import annotations

from collections.abc import Mapping
import sqlite3

from agentdeck.adapters.sqlite_schema import StoreSerializationError
from agentdeck.adapters.sqlite_validation import (
    _bounded_text,
    _canonical,
    _stored_timestamp,
)


_EFFECTS = frozenset({
    "read", "write_project", "command_project", "network", "write_external",
    "credential", "destructive", "publish",
})
_STATES = frozenset({"pending", "approved", "denied"})
_REQUEST_FIELDS = frozenset({
    "mission_id", "mission_version", "task_id", "attempt_id", "agent_id",
    "permission_request_id", "effect", "risk",
})
_DECISION_FIELDS = frozenset({"reviewer_id", "allowed", "reason"})


def _typed(value: object, field: str, prefix: str) -> str:
    text = _bounded_text(value, field, 255)
    if not text.startswith(prefix) or not text[len(prefix):] or any(c.isspace() for c in text):
        raise StoreSerializationError(f"{field} must be a typed identity")
    return text


def _approval_record(snapshot: Mapping[str, object], now: str) -> dict[str, object]:
    _, facts = _canonical(snapshot)
    fields = {
        "approval_id", "mission_id", "mission_version", "attempt_id", "effect",
        "state", "scope_hash", "request", "decision", "requested_at", "decided_at",
    }
    if set(facts) != fields:
        raise StoreSerializationError("approval snapshot fields are invalid")
    approval_id = _typed(facts["approval_id"], "approval_id", "apv_")
    mission_id = _typed(facts["mission_id"], "mission_id", "msn_")
    attempt_id = _typed(facts["attempt_id"], "attempt_id", "att_")
    version = facts["mission_version"]
    if type(version) is not int or version < 1:
        raise StoreSerializationError("mission_version must be positive")
    effect = _bounded_text(facts["effect"], "effect", 64)
    state = _bounded_text(facts["state"], "approval state", 32)
    if effect not in _EFFECTS or state not in _STATES:
        raise StoreSerializationError("approval effect or state is unsupported")
    scope_hash = _bounded_text(facts["scope_hash"], "scope_hash", 64)
    if len(scope_hash) != 64 or any(c not in "0123456789abcdef" for c in scope_hash):
        raise StoreSerializationError("scope_hash must be 64 lowercase hex")
    request_text, request = _canonical(facts["request"])
    if set(request) != _REQUEST_FIELDS:
        raise StoreSerializationError("approval request fields are invalid")
    _validate_request(request, mission_id, version, attempt_id, effect)
    requested_at = _stored_timestamp(facts.get("requested_at", now), "requested_at")
    decision_value, decided_value = facts["decision"], facts["decided_at"]
    if state == "pending":
        if decision_value is not None or decided_value is not None:
            raise StoreSerializationError("pending approval cannot have a decision")
        decision_text = None
        decided_at = None
    else:
        decision_text, decision = _canonical(decision_value)
        if set(decision) != _DECISION_FIELDS:
            raise StoreSerializationError("approval decision fields are invalid")
        _validate_decision(decision, state)
        decided_at = _stored_timestamp(decided_value, "decided_at")
        if decided_at < requested_at:
            raise StoreSerializationError("approval decided before request")
    return {
        "approval_id": approval_id, "mission_id": mission_id,
        "mission_version": version, "attempt_id": attempt_id, "effect": effect,
        "state": state, "scope_hash": scope_hash, "request_text": request_text,
        "request": request,
        "decision_text": decision_text, "requested_at": requested_at,
        "decided_at": decided_at,
    }


def _validate_request(
    request: dict[str, object], mission_id: str, version: int,
    attempt_id: str, effect: str,
) -> None:
    if (
        request["mission_id"] != mission_id
        or request["mission_version"] != version
        or request["attempt_id"] != attempt_id
        or request["effect"] != effect
    ):
        raise StoreSerializationError("approval request lineage drifted")
    _typed(request["task_id"], "request task_id", "tsk_")
    _typed(request["agent_id"], "request agent_id", "agt_")
    _typed(request["permission_request_id"], "permission_request_id", "perm_")
    _bounded_text(request["risk"], "approval risk", 2_048)


def _validate_decision(decision: dict[str, object], state: str) -> None:
    _bounded_text(decision["reviewer_id"], "reviewer_id", 255)
    _bounded_text(decision["reason"], "decision reason", 2_048)
    if type(decision["allowed"]) is not bool:
        raise StoreSerializationError("decision allowed must be a bool")
    if decision["allowed"] is not (state == "approved"):
        raise StoreSerializationError("approval state and decision disagree")


def _save_approval(
    connection: sqlite3.Connection, snapshot: Mapping[str, object], now: str
) -> None:
    record = _approval_record(snapshot, now)
    _validate_durable_lineage(connection, record)
    identity = record["approval_id"]
    existing = connection.execute(
        """SELECT mission_id,mission_version,attempt_id,effect,state,scope_hash,
                  canonical_request_facts,canonical_decision_facts,requested_at,decided_at
           FROM approvals WHERE approval_id=?""",
        (identity,),
    ).fetchone()
    immutable = (
        record["mission_id"], record["mission_version"], record["attempt_id"],
        record["effect"], record["scope_hash"], record["request_text"],
        record["requested_at"],
    )
    if existing is None:
        connection.execute(
            "INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                identity, record["mission_id"], record["mission_version"],
                record["attempt_id"], record["effect"], record["state"],
                record["scope_hash"], record["request_text"],
                record["decision_text"], record["requested_at"], record["decided_at"],
            ),
        )
        return
    old_immutable = (
        existing[0], existing[1], existing[2], existing[3], existing[5],
        existing[6], existing[8],
    )
    if immutable != old_immutable:
        raise ValueError("approval immutable lineage cannot drift")
    if existing[4] != "pending":
        candidate = (record["state"], record["decision_text"], record["decided_at"])
        if candidate != (existing[4], existing[7], existing[9]):
            raise ValueError("terminal approval decision cannot drift")
        return
    if record["state"] == "pending":
        return
    connection.execute(
        """UPDATE approvals SET state=?,canonical_decision_facts=?,decided_at=?
           WHERE approval_id=? AND state='pending'""",
        (record["state"], record["decision_text"], record["decided_at"], identity),
    )


def _validate_durable_lineage(
    connection: sqlite3.Connection, record: dict[str, object]
) -> None:
    request = record["request"]
    row = connection.execute(
        """SELECT a.task_id,a.agent_instance_id,t.mission_id,t.mission_version,
                  t.planned_agent_instance_id
           FROM attempts a JOIN tasks t ON t.task_id=a.task_id
           WHERE a.attempt_id=?""",
        (record["attempt_id"],),
    ).fetchone()
    expected = (
        request["task_id"], request["agent_id"], record["mission_id"],
        record["mission_version"], request["agent_id"],
    )
    if row is None or tuple(row) != expected:
        raise StoreSerializationError("approval durable lineage is inconsistent")


__all__ = ["_save_approval"]

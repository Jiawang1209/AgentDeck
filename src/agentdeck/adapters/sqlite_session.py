"""ProductSession row validation and SQLite persistence delegation."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import json
from pathlib import Path
import sqlite3

from agentdeck.adapters.sqlite_schema import (
    StoreCommandStateError,
    StoreSerializationError,
)
from agentdeck.adapters.sqlite_validation import (
    _ATTEMPT_COLUMNS,
    _MAX_STRING_BYTES,
    _attempt_fingerprint,
    _attempt_from_row,
    _bounded_text,
    _canonical,
    _stored_timestamp,
)
from agentdeck.kernel.execution import AttemptState
from agentdeck.kernel.session import ExitAttemptSnapshot, ExitRequest
from agentdeck.ports.store import CommandResult, SessionSelection, _session_identity


SESSION_COLUMNS = (
    "session_id", "project_id", "state", "permission_profile", "pending_goal",
    "created_at", "updated_at", "leader_backend", "leader_model",
    "pending_exit_id", "pending_exit_attempt_id",
    "canonical_pending_exit_attempt_facts", "pending_exit_attempt_hash",
    "pending_exit_requested_at",
)
_PAIR_COLUMNS = SESSION_COLUMNS[7:9]
_PENDING_COLUMNS = SESSION_COLUMNS[9:]
_TERMINAL_STATES = ("completed", "failed", "cancelled")
_SESSION_STATES = frozenset({
    "setup", "ready", "drafting", "awaiting_confirmation", "running",
    "awaiting_approval", "paused", "needs_attention", "completed", "failed",
    "cancelled",
})
_PERMISSION_PROFILES = frozenset({
    "ask_for_approval", "approve_for_me", "full_access",
})


def _session_record(
    snapshot: Mapping[str, object], default_project_id: str, now: str,
    existing: tuple[object, ...] | None,
) -> tuple[object, ...]:
    _, facts = _canonical(snapshot)
    allowed = set(SESSION_COLUMNS)
    if not set(facts) <= allowed:
        raise StoreSerializationError("session snapshot contains unsupported fields")
    try:
        session_id = _session_identity(facts.get("session_id"))
    except (TypeError, ValueError) as error:
        raise StoreSerializationError("session_id must be a typed identity") from error
    state = _bounded_text(facts.get("state"), "session state", 64)
    if state not in _SESSION_STATES:
        raise StoreSerializationError("session state is unsupported")
    old = _session_values(existing) if existing is not None else {}
    project_id = _bounded_text(
        facts.get("project_id", old.get("project_id", default_project_id)),
        "project_id", 255,
    )
    permission = facts.get("permission_profile")
    if permission is not None and permission not in _PERMISSION_PROFILES:
        raise StoreSerializationError("permission_profile is unsupported")
    pending_goal = facts.get("pending_goal")
    if pending_goal is not None:
        _bounded_text(pending_goal, "pending_goal", _MAX_STRING_BYTES)
    created_at = facts.get("created_at", old.get("created_at", now))
    updated_at = facts.get("updated_at", now)
    _stored_timestamp(created_at, "session created_at")
    _stored_timestamp(updated_at, "session updated_at")
    if existing is not None and (
        project_id != old["project_id"] or created_at != old["created_at"]
    ):
        raise ValueError("session immutable lineage cannot drift")
    if datetime.fromisoformat(updated_at) < datetime.fromisoformat(created_at):
        raise ValueError("session updated_at precedes created_at")
    leader, model = _pair_values(facts, old, state)
    pending = _pending_values(facts, old)
    return (
        session_id, project_id, state, permission, pending_goal, created_at, updated_at,
        leader, model, *pending,
    )


def _pair_values(
    facts: Mapping[str, object], old: Mapping[str, object], state: str,
) -> tuple[object, object]:
    supplied = tuple(column in facts for column in _PAIR_COLUMNS)
    if any(supplied) and not all(supplied):
        raise StoreSerializationError("session configuration identity must be a closed pair")
    if all(supplied):
        pair = (facts["leader_backend"], facts["leader_model"])
    else:
        pair = tuple(old.get(column) for column in _PAIR_COLUMNS)
    leader, model = pair
    if (leader is None) != (model is None):
        raise StoreSerializationError("session configuration identity must be a closed pair")
    if leader is not None:
        _bounded_text(leader, "leader_backend", 4_096)
        _bounded_text(model, "leader_model", 4_096)
    if state == "setup" and leader is not None:
        raise StoreSerializationError("setup session cannot have configuration identity")
    if state != "setup" and leader is None:
        raise StoreSerializationError("configured session requires configuration identity")
    if old and old.get("leader_backend") is not None and pair != tuple(
        old[column] for column in _PAIR_COLUMNS
    ):
        raise ValueError("session configuration identity cannot drift")
    return leader, model


def _pending_values(
    facts: Mapping[str, object], old: Mapping[str, object],
) -> tuple[object, ...]:
    supplied = tuple(column in facts for column in _PENDING_COLUMNS)
    if any(supplied) and not all(supplied):
        raise StoreSerializationError("pending exit facts must be a closed group")
    values = (
        tuple(facts[column] for column in _PENDING_COLUMNS)
        if all(supplied)
        else tuple(old.get(column) for column in _PENDING_COLUMNS)
    )
    null_count = sum(value is None for value in values)
    if null_count not in {0, len(_PENDING_COLUMNS)}:
        raise StoreSerializationError("pending exit facts must be a closed group")
    if null_count == 0:
        exit_id, attempt_id, canonical_facts, fingerprint, requested_at = values
        _bounded_text(exit_id, "pending_exit_id", 255)
        _bounded_text(attempt_id, "pending_exit_attempt_id", 255)
        _bounded_text(canonical_facts, "canonical_pending_exit_attempt_facts", 4_096)
        try:
            parsed = json.loads(canonical_facts)
            if type(parsed) is not dict:
                raise ValueError
            expected = {
                "attempt_id", "task_id", "agent_instance_id", "ordinal", "state",
                "acp_session_id", "effect_observed", "durable_fingerprint",
            }
            if set(parsed) != expected or type(parsed["state"]) is not str:
                raise ValueError
            snapshot = ExitAttemptSnapshot(
                attempt_id=parsed["attempt_id"], task_id=parsed["task_id"],
                agent_instance_id=parsed["agent_instance_id"],
                ordinal=parsed["ordinal"], state=AttemptState(parsed["state"]),
                acp_session_id=parsed["acp_session_id"],
                effect_observed=parsed["effect_observed"],
                durable_fingerprint=parsed["durable_fingerprint"],
            )
            request = ExitRequest(exit_id, snapshot, fingerprint, requested_at)
            if (
                attempt_id != snapshot.attempt_id
                or canonical_facts != snapshot.canonical_bytes().decode("utf-8")
                or requested_at != request.requested_at
            ):
                raise ValueError
        except (TypeError, ValueError, UnicodeError) as error:
            raise StoreSerializationError("pending exit facts are invalid") from error
    return values


def _session_values(row: tuple[object, ...]) -> dict[str, object]:
    values = dict(zip(SESSION_COLUMNS, row, strict=True))
    try:
        _session_identity(values["session_id"])
        _bounded_text(values["project_id"], "project_id", 255)
        state = _bounded_text(values["state"], "session state", 64)
        if state not in _SESSION_STATES:
            raise ValueError
        permission = values["permission_profile"]
        if permission is not None and permission not in _PERMISSION_PROFILES:
            raise ValueError
        pending_goal = values["pending_goal"]
        if pending_goal is not None:
            _bounded_text(pending_goal, "pending_goal", _MAX_STRING_BYTES)
        created = _stored_timestamp(values["created_at"], "session created_at")
        updated = _stored_timestamp(values["updated_at"], "session updated_at")
        if datetime.fromisoformat(updated) < datetime.fromisoformat(created):
            raise ValueError
        _pair_values(values, {}, state)
        _pending_values(values, {})
    except (TypeError, ValueError, StoreSerializationError) as error:
        raise StoreCommandStateError("stored session is malformed") from error
    return values


def load_session_aggregate(
    connection: sqlite3.Connection, session_id: str,
) -> CommandResult | None:
    row = connection.execute(
        f"SELECT {','.join(SESSION_COLUMNS)} FROM product_sessions WHERE session_id=?",
        (session_id,),
    ).fetchone()
    return None if row is None else _session_values(row)


def select_latest_nonterminal_session(
    connection: sqlite3.Connection, project_id: str,
) -> SessionSelection:
    placeholders = ",".join("?" for _ in _TERMINAL_STATES)
    predicate = f"project_id=? AND state NOT IN ({placeholders})"
    parameters = (project_id, *_TERMINAL_STATES)
    count = connection.execute(
        f"SELECT count(*) FROM product_sessions WHERE {predicate}", parameters
    ).fetchone()[0]
    row = connection.execute(
        f"""SELECT {','.join(SESSION_COLUMNS)} FROM product_sessions
            WHERE {predicate}
            ORDER BY updated_at DESC, created_at DESC, session_id DESC LIMIT 1""",
        parameters,
    ).fetchone()
    if row is None:
        return SessionSelection(None, count)
    values = _session_values(row)
    return SessionSelection(values["session_id"], count)


def list_active_exit_attempts(
    connection: sqlite3.Connection,
) -> tuple[ExitAttemptSnapshot, ...]:
    active = tuple(state.value for state in (
        AttemptState.RUNNING,
        AttemptState.AWAITING_APPROVAL,
        AttemptState.HUMAN_CONTROLLED,
    ))
    rows = connection.execute(
        f"""SELECT {','.join(_ATTEMPT_COLUMNS)} FROM attempts
            WHERE state IN ({','.join('?' for _ in active)}) ORDER BY attempt_id""",
        active,
    ).fetchall()
    validated = (_attempt_from_row(row)[1] for row in rows)
    return tuple(ExitAttemptSnapshot(
        attempt_id=row["attempt_id"], task_id=row["task_id"],
        agent_instance_id=row["agent_instance_id"], ordinal=row["ordinal"],
        state=AttemptState(row["state"]), acp_session_id=row["acp_session_id"],
        effect_observed=bool(row["effect_observed"]),
        durable_fingerprint=_attempt_fingerprint(row),
    ) for row in validated)


def save_session(
    connection: sqlite3.Connection,
    snapshot: Mapping[str, object],
    *,
    project_id: str,
    project_root: Path,
    now: str,
) -> None:
    if not isinstance(snapshot, Mapping):
        raise StoreSerializationError("session snapshot must be an object")
    session_id = snapshot.get("session_id")
    existing = connection.execute(
        f"SELECT {','.join(SESSION_COLUMNS)} FROM product_sessions WHERE session_id=?",
        (session_id,),
    ).fetchone() if type(session_id) is str else None
    record = _session_record(snapshot, project_id, now, existing)
    connection.execute(
        "INSERT OR IGNORE INTO projects VALUES (?, ?, ?)",
        (record[1], str(project_root), now),
    )
    connection.execute(
        f"""INSERT INTO product_sessions ({','.join(SESSION_COLUMNS)})
           VALUES ({','.join('?' for _ in SESSION_COLUMNS)})
           ON CONFLICT(session_id) DO UPDATE SET
             state=excluded.state, permission_profile=excluded.permission_profile,
             pending_goal=excluded.pending_goal, updated_at=excluded.updated_at,
             leader_backend=excluded.leader_backend,
             leader_model=excluded.leader_model,
             pending_exit_id=excluded.pending_exit_id,
             pending_exit_attempt_id=excluded.pending_exit_attempt_id,
             canonical_pending_exit_attempt_facts=
                 excluded.canonical_pending_exit_attempt_facts,
             pending_exit_attempt_hash=excluded.pending_exit_attempt_hash,
             pending_exit_requested_at=excluded.pending_exit_requested_at""",
        record,
    )

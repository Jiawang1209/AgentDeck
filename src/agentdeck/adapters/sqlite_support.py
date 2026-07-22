"""Read-only lineage enumeration for end-to-end Mission trace and support."""

from __future__ import annotations

import sqlite3

from agentdeck.adapters.sqlite_validation import _ATTEMPT_COLUMNS, _bounded_text


_TASK_COLUMNS = (
    "task_id", "mission_id", "mission_version", "ordinal", "name", "role",
    "planned_backend", "planned_agent_instance_id", "acp_route", "state",
    "canonical_task_facts", "created_at", "updated_at",
)
_HANDOFF_COLUMNS = (
    "handoff_id", "source_attempt_id", "target_task_id", "result_summary",
    "canonical_handoff_facts", "content_hash", "created_at",
)
_APPROVAL_COLUMNS = (
    "approval_id", "mission_id", "mission_version", "attempt_id", "effect",
    "state", "scope_hash", "canonical_request_facts",
    "canonical_decision_facts", "requested_at", "decided_at",
)
_EVIDENCE_COLUMNS = (
    "evidence_id", "task_id", "attempt_id", "kind",
    "canonical_evidence_facts", "content_hash", "created_at",
)


def list_mission_tasks(
    connection: sqlite3.Connection, mission_id: str,
) -> tuple[dict[str, object], ...]:
    """Return one Mission's Tasks ordered by ordinal; empty for an unknown Mission."""

    _bounded_text(mission_id, "mission_id", 255)
    rows = connection.execute(
        f"""SELECT {','.join(_TASK_COLUMNS)} FROM tasks
           WHERE mission_id=? ORDER BY ordinal, task_id""",
        (mission_id,),
    ).fetchall()
    return tuple(dict(zip(_TASK_COLUMNS, row, strict=True)) for row in rows)


def list_task_attempts(
    connection: sqlite3.Connection, task_id: str,
) -> tuple[dict[str, object], ...]:
    """Return one Task's Attempts ordered by ordinal."""

    _bounded_text(task_id, "task_id", 255)
    rows = connection.execute(
        f"""SELECT {','.join(_ATTEMPT_COLUMNS)} FROM attempts
           WHERE task_id=? ORDER BY ordinal""",
        (task_id,),
    ).fetchall()
    validated: list[dict[str, object]] = []
    for row in rows:
        record = dict(zip(_ATTEMPT_COLUMNS, row, strict=True))
        record["retryable"] = bool(record["retryable"])
        record["effect_observed"] = bool(record["effect_observed"])
        validated.append(record)
    return tuple(validated)


def list_attempt_handoffs(
    connection: sqlite3.Connection, attempt_id: str,
) -> tuple[dict[str, object], ...]:
    """Return the Handoffs sourced from one Attempt, deterministically ordered."""

    _bounded_text(attempt_id, "attempt_id", 255)
    rows = connection.execute(
        f"""SELECT {','.join(_HANDOFF_COLUMNS)} FROM handoffs
           WHERE source_attempt_id=? ORDER BY created_at, handoff_id""",
        (attempt_id,),
    ).fetchall()
    return tuple(dict(zip(_HANDOFF_COLUMNS, row, strict=True)) for row in rows)


def list_mission_approvals(
    connection: sqlite3.Connection, mission_id: str,
) -> tuple[dict[str, object], ...]:
    """Return one Mission's Approvals ordered by request time."""

    _bounded_text(mission_id, "mission_id", 255)
    rows = connection.execute(
        f"""SELECT {','.join(_APPROVAL_COLUMNS)} FROM approvals
           WHERE mission_id=? ORDER BY requested_at, approval_id""",
        (mission_id,),
    ).fetchall()
    return tuple(dict(zip(_APPROVAL_COLUMNS, row, strict=True)) for row in rows)


def list_attempt_evidence(
    connection: sqlite3.Connection, attempt_id: str,
) -> tuple[dict[str, object], ...]:
    """Return the Evidence recorded for one Attempt, deterministically ordered."""

    _bounded_text(attempt_id, "attempt_id", 255)
    rows = connection.execute(
        f"""SELECT {','.join(_EVIDENCE_COLUMNS)} FROM evidence
           WHERE attempt_id=? ORDER BY created_at, evidence_id""",
        (attempt_id,),
    ).fetchall()
    return tuple(dict(zip(_EVIDENCE_COLUMNS, row, strict=True)) for row in rows)


__all__ = [
    "list_attempt_evidence",
    "list_attempt_handoffs",
    "list_mission_approvals",
    "list_mission_tasks",
    "list_task_attempts",
]

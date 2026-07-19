"""Canonical Evidence and Handoff persistence for one execution transaction."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
import sqlite3

from agentdeck.adapters.sqlite_schema import StoreSerializationError
from agentdeck.adapters.sqlite_validation import _bounded_text, _canonical, _stored_timestamp
from agentdeck.kernel.execution import Evidence, EvidenceKind, Handoff


_EVIDENCE_FIELDS = frozenset({
    "evidence_id", "task_id", "attempt_id", "kind", "canonical_evidence_facts",
})
_HANDOFF_FIELDS = frozenset({
    "handoff_id", "source_attempt_id", "target_task_id", "result_summary",
    "canonical_handoff_facts", "content_hash",
})
_EXECUTION_READ_SPECS = {
    "attempts": ("attempt_id", "att_", (
        "attempt_id", "task_id", "agent_instance_id", "ordinal", "state",
        "reason", "result_summary", "retryable", "acp_session_id",
        "effect_observed",
    ), ("retryable", "effect_observed")),
    "evidence": ("evidence_id", "ev_", (
        "evidence_id", "task_id", "attempt_id", "kind",
        "canonical_evidence_facts",
    ), ()),
    "handoffs": ("handoff_id", "hnd_", (
        "handoff_id", "source_attempt_id", "target_task_id", "result_summary",
        "canonical_handoff_facts", "content_hash",
    ), ()),
}


def _typed(value: object, field: str, prefix: str) -> str:
    text = _bounded_text(value, field, 255)
    if not text.startswith(prefix) or not text[len(prefix):] or any(c.isspace() for c in text):
        raise StoreSerializationError(f"{field} must be a typed identity")
    return text


def load_execution_aggregate(
    connection: sqlite3.Connection, aggregate_type: str, aggregate_id: str,
) -> dict[str, object] | None:
    try:
        identity, prefix, columns, boolean_fields = _EXECUTION_READ_SPECS[
            aggregate_type
        ]
    except (KeyError, TypeError) as error:
        raise ValueError("unsupported execution aggregate type") from error
    checked_id = _typed(aggregate_id, identity, prefix)
    row = connection.execute(
        f"SELECT {','.join(columns)} FROM {aggregate_type} WHERE {identity}=?",
        (checked_id,),
    ).fetchone()
    if row is None:
        return None
    snapshot = dict(zip(columns, row, strict=True))
    for field in boolean_fields:
        snapshot[field] = bool(snapshot[field])
    return snapshot


def _save_evidence(
    connection: sqlite3.Connection, snapshot: Mapping[str, object], now: str
) -> None:
    _, facts = _canonical(snapshot)
    if set(facts) != _EVIDENCE_FIELDS:
        raise StoreSerializationError("evidence snapshot fields are invalid")
    identity = _typed(facts["evidence_id"], "evidence_id", "ev_")
    task_id = _typed(facts["task_id"], "task_id", "tsk_")
    attempt_id = _typed(facts["attempt_id"], "attempt_id", "att_")
    try:
        kind = EvidenceKind(facts["kind"])
        evidence = Evidence(identity, kind, facts["canonical_evidence_facts"])
    except (TypeError, ValueError) as error:
        raise StoreSerializationError("evidence canonical facts are invalid") from error
    lineage = connection.execute(
        """SELECT a.task_id,a.state,a.agent_instance_id,t.planned_agent_instance_id,
                  t.mission_id,t.mission_version,m.session_id,ai.session_id
           FROM attempts a JOIN tasks t ON t.task_id=a.task_id
           JOIN mission_versions v ON v.mission_id=t.mission_id
             AND v.version=t.mission_version
           JOIN missions m ON m.mission_id=t.mission_id
           JOIN agent_instances ai ON ai.instance_id=a.agent_instance_id
           WHERE a.attempt_id=?""",
        (attempt_id,),
    ).fetchone()
    if (
        lineage is None or lineage[0] != task_id or lineage[1] != "completed"
        or lineage[2] != lineage[3]
    ):
        raise StoreSerializationError("evidence durable lineage is inconsistent")
    if lineage[6] != lineage[7]:
        raise ValueError("attempt Agent does not belong to the mission session")
    content_hash = sha256(evidence.canonical_content.encode("utf-8")).hexdigest()
    created_at = _stored_timestamp(now, "evidence created_at")
    record = (
        identity, task_id, attempt_id, kind.value, evidence.canonical_content,
        content_hash, created_at,
    )
    existing = connection.execute(
        "SELECT evidence_id,task_id,attempt_id,kind,canonical_evidence_facts,content_hash,created_at FROM evidence WHERE evidence_id=?",
        (identity,),
    ).fetchone()
    if existing is not None:
        if tuple(existing) != record:
            raise ValueError("evidence immutable facts cannot drift")
        return
    connection.execute("INSERT INTO evidence VALUES (?,?,?,?,?,?,?)", record)


def _save_execution_aggregate(
    connection: sqlite3.Connection, aggregate_type: str, aggregate_id: str,
    snapshot: Mapping[str, object], now: str,
) -> None:
    field = "evidence_id" if aggregate_type == "evidence" else "handoff_id"
    if snapshot.get(field, aggregate_id) != aggregate_id:
        raise ValueError(f"aggregate identity does not match {field}")
    saver = _save_evidence if aggregate_type == "evidence" else _save_handoff
    saver(connection, snapshot, now)


def _save_handoff(
    connection: sqlite3.Connection, snapshot: Mapping[str, object], now: str
) -> None:
    _, facts = _canonical(snapshot)
    if set(facts) != _HANDOFF_FIELDS:
        raise StoreSerializationError("handoff snapshot fields are invalid")
    identity = _typed(facts["handoff_id"], "handoff_id", "hnd_")
    source = _typed(facts["source_attempt_id"], "source_attempt_id", "att_")
    target = _typed(facts["target_task_id"], "target_task_id", "tsk_")
    summary = _bounded_text(facts["result_summary"], "result_summary", 65_536)
    canonical = _bounded_text(
        facts["canonical_handoff_facts"], "canonical_handoff_facts", 262_144
    )
    content_hash = _bounded_text(facts["content_hash"], "content_hash", 64)
    handoff = _handoff_from_canonical(
        identity, source, target, summary, canonical, content_hash
    )
    lineage = connection.execute(
        """SELECT a.state,source.mission_id,source.mission_version,
                  target.mission_id,target.mission_version,a.agent_instance_id,
                  source.planned_agent_instance_id,source.task_id,
                  target.canonical_task_facts,target.planned_agent_instance_id,
                  mission.session_id,source_agent.session_id,target_agent.session_id
           FROM attempts a JOIN tasks source ON source.task_id=a.task_id
           JOIN tasks target ON target.task_id=?
           JOIN missions mission ON mission.mission_id=source.mission_id
           JOIN agent_instances source_agent ON source_agent.instance_id=a.agent_instance_id
           JOIN agent_instances target_agent
             ON target_agent.instance_id=target.planned_agent_instance_id
           WHERE a.attempt_id=?""",
        (target, source),
    ).fetchone()
    if (
        lineage is None or lineage[0] != "completed"
        or lineage[1:3] != lineage[3:5] or lineage[5] != lineage[6]
        or lineage[10] != lineage[11] or lineage[10] != lineage[12]
    ):
        raise ValueError("handoff durable lineage is inconsistent")
    _require_direct_dependency(
        lineage[8], target, lineage[7], lineage[9]
    )
    placeholders = ",".join("?" for _ in handoff.verification_evidence_ids)
    evidence_rows = connection.execute(
        f"SELECT evidence_id,attempt_id FROM evidence WHERE evidence_id IN ({placeholders})",
        handoff.verification_evidence_ids,
    ).fetchall()
    if {row[0] for row in evidence_rows} != set(handoff.verification_evidence_ids) or any(
        row[1] != source for row in evidence_rows
    ):
        raise ValueError("handoff evidence lineage is inconsistent")
    created_at = _stored_timestamp(now, "handoff created_at")
    record = (identity, source, target, summary, canonical, content_hash, created_at)
    existing = connection.execute(
        "SELECT handoff_id,source_attempt_id,target_task_id,result_summary,canonical_handoff_facts,content_hash,created_at FROM handoffs WHERE handoff_id=?",
        (identity,),
    ).fetchone()
    if existing is not None:
        if tuple(existing) != record:
            raise ValueError("handoff immutable facts cannot drift")
        return
    connection.execute("INSERT INTO handoffs VALUES (?,?,?,?,?,?,?)", record)


def _handoff_from_canonical(
    identity: str, source: str, target: str, summary: str,
    canonical: str, content_hash: str,
) -> Handoff:
    try:
        payload = json.loads(canonical)
        if type(payload) is not dict:
            raise ValueError
        return Handoff(
            identity, source, target, summary,
            tuple(payload["verification_evidence_ids"]),
            tuple(payload["artifact_references"]), tuple(payload["known_issues"]),
            canonical, content_hash,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise StoreSerializationError("handoff canonical facts are invalid") from error


def _require_direct_dependency(
    canonical: object, target_id: str, source_id: object, target_agent: object
) -> None:
    try:
        payload = json.loads(canonical)
        if (
            type(payload) is not dict
            or payload["task_id"] != target_id
            or payload["agent_instance_id"] != target_agent
            or payload["dependencies"] != [source_id]
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("handoff target is not the frozen direct dependency") from error


__all__ = ["_save_execution_aggregate", "load_execution_aggregate"]

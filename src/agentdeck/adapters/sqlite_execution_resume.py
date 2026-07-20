"""Strict read-only SQLite projection for explicit project resume."""

from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
from hmac import compare_digest
import json
import sqlite3

from agentdeck.adapters.sqlite_schema import StoreCommandStateError
from agentdeck.adapters.sqlite_validation import (
    _attempt_fingerprint,
    _attempt_from_row,
    _validate_command_row,
)
from agentdeck.kernel.events import normalize_occurred_at
from agentdeck.kernel.execution import (
    AcceptanceResult,
    Attempt,
    AttemptState,
    Evidence,
    EvidenceKind,
    FindingSeverity,
    Handoff,
    ReviewFinding,
)
from agentdeck.kernel.execution_semantics import materialize_revision
from agentdeck.kernel.mission import (
    ConfirmedMissionVersion,
    MissionDraft,
    TaskDefinition,
    _canonical_mission_draft,
)
from agentdeck.ports.execution_resume import (
    ExecutionResumeFacts,
    ExecutionResumeProjectionError,
    ExecutionResumeSnapshot,
    ResumeAttemptFacts,
    ResumeEvidenceFacts,
    ResumeHandoffFacts,
    ResumeStageFacts,
)


_ACTIVE_ATTEMPT_STATES = frozenset({
    AttemptState.PENDING,
    AttemptState.RUNNING,
    AttemptState.AWAITING_APPROVAL,
    AttemptState.HUMAN_CONTROLLED,
})
_SESSION_QUERY = """SELECT state,pending_exit_id,pending_exit_attempt_id,
    canonical_pending_exit_attempt_facts,pending_exit_attempt_hash,
    pending_exit_requested_at FROM product_sessions WHERE session_id=?"""
_MISSION_QUERY = """SELECT mission_id,state,current_version FROM missions
 WHERE session_id=? AND state IN ('confirmed','running')"""
_VERSION_QUERY = """SELECT version,content_hash,canonical_mission_facts,confirmed_at
 FROM mission_versions WHERE mission_id=? AND version=?"""
_TASK_QUERY = """SELECT task_id,ordinal,name,role,planned_backend,
    planned_agent_instance_id,acp_route,state,canonical_task_facts
  FROM tasks WHERE mission_id=? AND mission_version=? ORDER BY ordinal"""
_ATTEMPT_QUERY = """SELECT a.attempt_id,a.task_id,a.agent_instance_id,a.ordinal,
    a.state,a.reason,a.result_summary,a.retryable,a.acp_session_id,
    a.effect_observed,a.created_at,a.updated_at
  FROM attempts AS a JOIN tasks AS t ON t.task_id=a.task_id
 WHERE t.mission_id=? AND t.mission_version=?
 ORDER BY t.ordinal,a.ordinal"""
def _fail(code: str = "resume_projection_malformed") -> None:
    raise ExecutionResumeProjectionError(code=code)
def _typed_session_id(value: object) -> str:
    if type(value) is not str:
        _fail()
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        _fail()
    if (
        not value.startswith("ses_") or not value[4:] or len(encoded) > 255
        or any(character.isspace() for character in value)
    ):
        _fail()
    return value
def _canonical(value: object) -> str:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        _fail()
def _load_session(connection: sqlite3.Connection, session_id: str) -> None:
    row = connection.execute(_SESSION_QUERY, (session_id,)).fetchone()
    if row is None or row[0] != "paused":
        _fail("resume_session_not_paused")
    if any(value is not None for value in row[1:]):
        _fail("resume_pending_exit")
def _load_mission(
    connection: sqlite3.Connection, session_id: str,
) -> tuple[ConfirmedMissionVersion, MissionDraft]:
    rows = connection.execute(_MISSION_QUERY, (session_id,)).fetchall()
    if not rows:
        _fail("resume_mission_missing")
    if len(rows) != 1:
        _fail("resume_mission_ambiguous")
    mission_id, state, current = rows[0]
    versions = connection.execute(
        _VERSION_QUERY, (mission_id, current)
    ).fetchall()
    if state not in {"confirmed", "running"} or len(versions) != 1:
        _fail()
    version, content_hash, canonical, confirmed_at = versions[0]
    if current != version:
        _fail()
    normalize_occurred_at(confirmed_at)
    confirmed = ConfirmedMissionVersion(
        mission_id, version, content_hash, canonical
    )
    draft = _canonical_mission_draft(canonical, version)
    if len(draft.tasks) != 4:
        _fail()
    return confirmed, draft
def _load_tasks(
    connection: sqlite3.Connection,
    session_id: str,
    confirmed: ConfirmedMissionVersion,
    draft: MissionDraft,
) -> tuple[tuple[object, ...], ...]:
    rows = connection.execute(
        _TASK_QUERY, (confirmed.mission_id, confirmed.version)
    ).fetchall()
    if len(rows) != 4:
        _fail()
    agent_ids: set[str] = set()
    for ordinal, (row, task) in enumerate(zip(rows, draft.tasks, strict=True), 1):
        expected_canonical = _canonical(task.canonical_projection())
        expected = (
            task.task_id, ordinal, task.name, task.role.value, task.backend,
            task.agent_instance_id, task.acp_route,
        )
        if row[:7] != expected or row[8] != expected_canonical:
            _fail()
        agent = connection.execute(
            """SELECT session_id,backend_id,transport,role,acp_session_id
                 FROM agent_instances WHERE instance_id=?""",
            (task.agent_instance_id,),
        ).fetchone()
        if agent is None or agent[:4] != (
            session_id, task.backend, "acp", task.role.value
        ):
            _fail()
        agent_ids.add(task.agent_instance_id)
    if len(agent_ids) != 4:
        _fail()
    return tuple(rows)
def _load_attempts(
    connection: sqlite3.Connection,
    confirmed: ConfirmedMissionVersion,
    draft: MissionDraft,
) -> dict[str, tuple[tuple[Attempt, dict[str, object]], ...]]:
    rows = connection.execute(
        _ATTEMPT_QUERY, (confirmed.mission_id, confirmed.version)
    ).fetchall()
    grouped: defaultdict[str, list[tuple[Attempt, dict[str, object]]]] = defaultdict(list)
    tasks = {task.task_id: task for task in draft.tasks}
    for row in rows:
        attempt, values = _attempt_from_row(row)
        task = tasks.get(attempt.task_id)
        if task is None or values["agent_instance_id"] != task.agent_instance_id:
            _fail()
        agent_acp = connection.execute(
            "SELECT acp_session_id FROM agent_instances WHERE instance_id=?",
            (task.agent_instance_id,),
        ).fetchone()
        if agent_acp is None or values["acp_session_id"] != agent_acp[0]:
            _fail()
        if attempt.state in _ACTIVE_ATTEMPT_STATES:
            _fail()
        if attempt.state is AttemptState.OUTCOME_UNKNOWN:
            _fail("resume_outcome_unknown")
        grouped[attempt.task_id].append((attempt, values))
    for task_id, attempts in grouped.items():
        ordinals = tuple(attempt.ordinal for attempt, _ in attempts)
        if ordinals != tuple(range(1, len(attempts) + 1)):
            _fail()
        if any(
            attempt.state is AttemptState.COMPLETED
            for attempt, _ in attempts[:-1]
        ):
            _fail()
        if attempts[-1][0].state is AttemptState.COMPLETED and any(
            attempt.state not in {AttemptState.FAILED, AttemptState.INTERRUPTED}
            or (attempt.state is AttemptState.FAILED and not attempt.retryable)
            for attempt, _ in attempts[:-1]
        ):
            _fail()
        grouped[task_id] = attempts
    return {key: tuple(value) for key, value in grouped.items()}
def _command_id(
    confirmed: ConfirmedMissionVersion, task: TaskDefinition, ordinal: int,
) -> str:
    parts = (
        confirmed.mission_id, str(confirmed.version), task.task_id,
        "terminal", str(ordinal),
    )
    canonical = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return "cmd_" + sha256(canonical.encode("utf-8")).hexdigest()[:24]
def _load_terminal_command(
    connection: sqlite3.Connection,
    confirmed: ConfirmedMissionVersion,
    task: TaskDefinition,
    attempt: Attempt,
) -> tuple[str, str, dict[str, object]]:
    identity = _command_id(confirmed, task, attempt.ordinal)
    row = connection.execute(
        """SELECT command_kind,state,canonical_result_facts,created_at,completed_at
             FROM commands WHERE command_id=?""",
        (identity,),
    ).fetchone()
    if row is None or row[0] != "execution_stage_committed":
        _fail()
    result = _validate_command_row(row)
    expected_base = {
        "mission_id": confirmed.mission_id,
        "mission_version": confirmed.version,
        "task_id": task.task_id,
        "attempt_id": attempt.attempt_id,
    }
    if type(result) is not dict or set(result) != {
        *expected_base, "evidence_ids", "handoff_id"
    } or any(result[field] != value for field, value in expected_base.items()):
        _fail()
    evidence_ids = result["evidence_ids"]
    if (
        type(evidence_ids) is not list or not evidence_ids
        or any(type(item) is not str for item in evidence_ids)
        or len(evidence_ids) != len(set(evidence_ids))
        or (result["handoff_id"] is not None and type(result["handoff_id"]) is not str)
    ):
        _fail()
    canonical_result = row[2]
    return identity, sha256(canonical_result.encode("utf-8")).hexdigest(), result
def _load_evidence(
    connection: sqlite3.Connection,
    task: TaskDefinition,
    attempt: Attempt,
    identities: list[str],
) -> tuple[ResumeEvidenceFacts, ...]:
    placeholders = ",".join("?" for _ in identities)
    rows = connection.execute(
        f"""SELECT evidence_id,task_id,attempt_id,kind,
                   canonical_evidence_facts,content_hash
              FROM evidence WHERE evidence_id IN ({placeholders})""",
        identities,
    ).fetchall()
    by_id = {row[0]: row for row in rows}
    if set(by_id) != set(identities) or len(rows) != len(identities):
        _fail()
    all_ids = tuple(row[0] for row in connection.execute(
        "SELECT evidence_id FROM evidence WHERE attempt_id=? ORDER BY evidence_id",
        (attempt.attempt_id,),
    ))
    if set(all_ids) != set(identities) or len(all_ids) != len(identities):
        _fail()
    facts: list[ResumeEvidenceFacts] = []
    for identity in identities:
        row = by_id[identity]
        if row[1:3] != (task.task_id, attempt.attempt_id):
            _fail()
        evidence = Evidence(row[0], EvidenceKind(row[3]), row[4])
        expected_hash = sha256(evidence.canonical_content.encode("utf-8")).hexdigest()
        if not compare_digest(row[5], expected_hash):
            _fail()
        facts.append(ResumeEvidenceFacts(*row))
    return tuple(facts)
def _load_handoff(
    connection: sqlite3.Connection,
    draft: MissionDraft,
    task: TaskDefinition,
    next_task: TaskDefinition | None,
    attempt: Attempt,
    evidence: tuple[ResumeEvidenceFacts, ...],
    identity: object,
) -> ResumeHandoffFacts | None:
    rows = connection.execute(
        """SELECT handoff_id,source_attempt_id,target_task_id,result_summary,
                  canonical_handoff_facts,content_hash
             FROM handoffs WHERE source_attempt_id=?""",
        (attempt.attempt_id,),
    ).fetchall()
    if next_task is None:
        if identity is not None or rows:
            _fail()
        return None
    if type(identity) is not str or len(rows) != 1 or rows[0][0] != identity:
        _fail()
    row = rows[0]
    try:
        payload = json.loads(row[4])
        handoff = Handoff(
            row[0], row[1], row[2], row[3],
            tuple(payload["verification_evidence_ids"]),
            tuple(payload["artifact_references"]),
            tuple(payload["known_issues"]), row[4], row[5],
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        _fail()
    expected_evidence_ids = tuple(item.evidence_id for item in evidence)
    if task.name == "review":
        findings = tuple(_review_finding(item) for item in evidence)
        materialized = materialize_revision(
            findings=findings, confirmed_scope=(draft.scope,)
        )
        indexed = {
            finding.finding_id: item.evidence_id
            for finding, item in zip(findings, evidence, strict=True)
        }
        expected_evidence_ids = tuple(
            indexed[finding.finding_id] for finding in materialized.findings
        )
        if not expected_evidence_ids:
            _fail()
    if (
        handoff.source_attempt_id != attempt.attempt_id
        or handoff.target_task_id != next_task.task_id
        or handoff.result_summary != attempt.result_summary
        or handoff.verification_evidence_ids != expected_evidence_ids
    ):
        _fail()
    return ResumeHandoffFacts(*row)
def _review_finding(item: ResumeEvidenceFacts) -> ReviewFinding:
    if item.kind != EvidenceKind.REVIEW_FINDING.value:
        _fail()
    payload = json.loads(item.canonical_evidence_facts)
    return ReviewFinding(
        payload["finding_id"], payload["scope"], FindingSeverity(payload["severity"]),
        payload["summary"], payload["criterion"], tuple(payload["evidence_ids"]),
    )
def _validate_acceptance(
    draft: MissionDraft,
    task: TaskDefinition,
    evidence: tuple[ResumeEvidenceFacts, ...],
) -> None:
    if task.name != "acceptance":
        return
    if len(evidence) != 1 or evidence[0].kind != EvidenceKind.ACCEPTANCE_RESULT.value:
        _fail()
    payload = json.loads(evidence[0].canonical_evidence_facts)
    result = AcceptanceResult.create(
        payload["criteria"], payload["evidence_by_criterion"],
        accepted=payload["accepted"], failure_reason=payload["failure_reason"],
    )
    if not result.accepted or result.criteria != draft.acceptance_criteria:
        _fail()
def _cross_stage_reference_ids(
    evidence: ResumeEvidenceFacts,
) -> tuple[str, ...]:
    payload = json.loads(evidence.canonical_evidence_facts)
    if evidence.kind == EvidenceKind.REVIEW_FINDING.value:
        return tuple(payload["evidence_ids"])
    if evidence.kind == EvidenceKind.ACCEPTANCE_RESULT.value:
        return tuple(
            identity
            for identities in payload["evidence_by_criterion"].values()
            for identity in identities
        )
    return ()
def _attempt_facts(
    attempts: tuple[tuple[Attempt, dict[str, object]], ...],
) -> tuple[ResumeAttemptFacts, ...]:
    return tuple(ResumeAttemptFacts(
        attempt.attempt_id, attempt.task_id, values["agent_instance_id"],
        attempt.ordinal, attempt.state.value, attempt.reason,
        attempt.result_summary, attempt.retryable, values["acp_session_id"],
        bool(values["effect_observed"]), _attempt_fingerprint(values),
    ) for attempt, values in attempts)
def _stage_facts(
    connection: sqlite3.Connection,
    confirmed: ConfirmedMissionVersion,
    draft: MissionDraft,
    rows: tuple[tuple[object, ...], ...],
    attempts_by_task: dict[str, tuple[tuple[Attempt, dict[str, object]], ...]],
) -> tuple[ResumeStageFacts, ...]:
    stages: list[ResumeStageFacts] = []
    preceding_evidence_ids: set[str] = set()
    for index, (row, task) in enumerate(zip(rows, draft.tasks, strict=True)):
        attempts = attempts_by_task.get(task.task_id, ())
        command_id = command_hash = terminal_attempt_id = None
        evidence: tuple[ResumeEvidenceFacts, ...] = ()
        handoff = None
        if attempts and attempts[-1][0].state is AttemptState.COMPLETED:
            terminal = attempts[-1][0]
            command_id, command_hash, result = _load_terminal_command(
                connection, confirmed, task, terminal
            )
            terminal_attempt_id = terminal.attempt_id
            evidence = _load_evidence(
                connection, task, terminal, result["evidence_ids"]
            )
            for item in evidence:
                references = _cross_stage_reference_ids(item)
                if references and (
                    not set(references) <= preceding_evidence_ids
                ):
                    _fail()
            next_task = draft.tasks[index + 1] if index + 1 < len(draft.tasks) else None
            handoff = _load_handoff(
                connection, draft, task, next_task, terminal, evidence,
                result["handoff_id"],
            )
            _validate_acceptance(draft, task, evidence)
            preceding_evidence_ids = {item.evidence_id for item in evidence}
        elif attempts and attempts[-1][0].state is not AttemptState.INTERRUPTED:
            _fail("resume_stage_not_retryable")
        stages.append(ResumeStageFacts(
            row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7],
            row[8], _attempt_facts(attempts), command_id, command_hash,
            terminal_attempt_id, evidence, handoff,
        ))
    return tuple(stages)
def _validate_command_coverage(
    connection: sqlite3.Connection, confirmed: ConfirmedMissionVersion,
    draft: MissionDraft, stages: tuple[ResumeStageFacts, ...],
) -> None:
    represented = {stage.terminal_command_id: stage for stage in stages
                   if stage.terminal_command_id is not None}
    seen: set[str] = set()
    def validate(identity: str, row: tuple[object, ...]) -> None:
        stage = represented.get(identity)
        if stage is None or row[0] != "execution_stage_committed":
            _fail()
        result = _validate_command_row(row)
        expected = {
            "mission_id": confirmed.mission_id, "mission_version": confirmed.version,
            "task_id": stage.task_id, "attempt_id": stage.terminal_attempt_id,
            "evidence_ids": [item.evidence_id for item in stage.evidence],
            "handoff_id": None if stage.handoff is None else stage.handoff.handoff_id}
        if set(result) != set(expected) or result != expected or (
            sha256(row[2].encode("utf-8")).hexdigest() != stage.terminal_command_hash
        ):
            _fail()
        seen.add(identity)
    for task, stage in zip(draft.tasks, stages, strict=True):
        last = stage.attempts[-1].ordinal if stage.attempts else 0
        maximum = last + 1 if stage.terminal_command_id is None else last
        maximum = max(maximum, 1)
        for ordinal in range(1, maximum + 1):
            identity = _command_id(confirmed, task, ordinal)
            row = connection.execute(
                """SELECT command_kind,state,canonical_result_facts,
                          created_at,completed_at FROM commands WHERE command_id=?""",
                (identity,),
            ).fetchone()
            if row is None:
                continue
            validate(identity, row)
    rows = connection.execute("""SELECT command_id,command_kind,state,canonical_result_facts,
                  created_at,completed_at FROM commands
             WHERE command_kind='execution_stage_committed'""")
    for identity, *row in rows:
        decoded = _validate_command_row(tuple(row))
        mission_id, mission_version = decoded.get("mission_id"), decoded.get("mission_version")
        if (type(mission_id) is not str or not mission_id.strip()
                or type(mission_version) is not int or mission_version < 1):
            _fail()
        if (mission_id, mission_version) == (confirmed.mission_id, confirmed.version):
            validate(identity, tuple(row))
    if seen != set(represented):
        _fail()
def load_execution_resume(
    connection: sqlite3.Connection, session_id: str,
) -> ExecutionResumeSnapshot:
    """Derive exact resume authority without changing SQLite."""

    if not isinstance(connection, sqlite3.Connection):
        raise TypeError("resume projection requires a SQLite connection")
    checked_session_id = _typed_session_id(session_id)
    try:
        _load_session(connection, checked_session_id)
        confirmed, draft = _load_mission(connection, checked_session_id)
        task_rows = _load_tasks(
            connection, checked_session_id, confirmed, draft
        )
        attempts = _load_attempts(connection, confirmed, draft)
        stages = _stage_facts(
            connection, confirmed, draft, task_rows, attempts
        )
        _validate_command_coverage(connection, confirmed, draft, stages)
        facts = ExecutionResumeFacts(
            checked_session_id, "paused", confirmed.mission_id,
            confirmed.version, confirmed.content_hash,
            confirmed.canonical_content, stages,
        )
        return ExecutionResumeSnapshot.create(facts)
    except ExecutionResumeProjectionError:
        raise
    except (KeyError, TypeError, ValueError, StoreCommandStateError, sqlite3.Error):
        _fail()


__all__ = ["load_execution_resume"]

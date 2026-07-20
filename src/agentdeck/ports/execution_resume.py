"""Immutable, content-free durable execution resume projection values."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from hmac import compare_digest
import json
from typing import Final


EXECUTION_RESUME_MAX_BYTES: Final = 1_048_576
_TEXT_MAX_BYTES: Final = 65_536
_CANONICAL_MAX_BYTES: Final = 262_144
_SQLITE_MAX_INTEGER: Final = 2**63 - 1
_LOWER_HEX: Final = frozenset("0123456789abcdef")
_ATTEMPT_STATES: Final = frozenset({
    "pending", "running", "awaiting_approval", "human_controlled", "completed",
    "failed", "cancelled", "interrupted", "outcome_unknown",
})
_TASK_STATES: Final = frozenset({
    "pending", "ready", "running", "awaiting_approval", "completed", "failed",
    "cancelled",
})
_ROLES: Final = frozenset({
    "implementer", "reviewer", "reviser", "acceptance_reviewer",
})
_EVIDENCE_KINDS: Final = frozenset({
    "test_exit_status", "diff_identity", "artifact_hash", "review_finding",
    "acceptance_result", "human_decision",
})


class ExecutionResumeProjectionError(ValueError):
    ALLOWED_CODES = frozenset({
        "resume_session_not_paused",
        "resume_pending_exit",
        "resume_mission_missing",
        "resume_mission_ambiguous",
        "resume_projection_malformed",
        "resume_outcome_unknown",
        "resume_stage_not_retryable",
        "resume_ordinal_exhausted",
        "resume_mission_complete",
    })

    def __init__(self, *, code: str) -> None:
        if type(code) is not str or code not in self.ALLOWED_CODES:
            raise ValueError("resume projection code is not allowlisted")
        self.code = code
        super().__init__(code)


def _malformed() -> None:
    raise ExecutionResumeProjectionError(code="resume_projection_malformed")


def _text(value: object, *, maximum: int = _TEXT_MAX_BYTES) -> str:
    if type(value) is not str or not value.strip():
        _malformed()
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        _malformed()
    if len(encoded) > maximum:
        _malformed()
    return value


def _optional_text(value: object) -> str | None:
    return None if value is None else _text(value)


def _identity(value: object, prefix: str) -> str:
    text = _text(value, maximum=255)
    if not text.startswith(prefix) or not text[len(prefix):] or any(
        character.isspace() for character in text
    ):
        _malformed()
    return text


def _optional_identity(value: object, prefix: str) -> str | None:
    return None if value is None else _identity(value, prefix)


def _integer(value: object, *, allow_zero: bool = False) -> int:
    minimum = 0 if allow_zero else 1
    if type(value) is not int or not minimum <= value <= _SQLITE_MAX_INTEGER:
        _malformed()
    return value


def _bool(value: object) -> bool:
    if type(value) is not bool:
        _malformed()
    return value


def _hash(value: object) -> str:
    if (
        type(value) is not str or len(value) != 64
        or any(character not in _LOWER_HEX for character in value)
    ):
        _malformed()
    return value


def _choice(value: object, choices: frozenset[str]) -> str:
    text = _text(value, maximum=64)
    if text not in choices:
        _malformed()
    return text


def _tuple(value: object, item_type: type) -> tuple:
    if type(value) is not tuple or any(type(item) is not item_type for item in value):
        _malformed()
    return value


@dataclass(frozen=True)
class ResumeAttemptFacts:
    attempt_id: str
    task_id: str
    agent_instance_id: str | None
    ordinal: int
    state: str
    reason: str | None
    result_summary: str | None
    retryable: bool
    acp_session_id: str | None
    effect_observed: bool
    durable_fingerprint: str

    def __post_init__(self) -> None:
        _identity(self.attempt_id, "att_")
        _identity(self.task_id, "tsk_")
        _optional_identity(self.agent_instance_id, "agt_")
        _integer(self.ordinal)
        _choice(self.state, _ATTEMPT_STATES)
        _optional_text(self.reason)
        _optional_text(self.result_summary)
        _bool(self.retryable)
        _optional_text(self.acp_session_id)
        _bool(self.effect_observed)
        _hash(self.durable_fingerprint)


@dataclass(frozen=True)
class ResumeEvidenceFacts:
    evidence_id: str
    task_id: str
    attempt_id: str
    kind: str
    canonical_evidence_facts: str
    content_hash: str

    def __post_init__(self) -> None:
        _identity(self.evidence_id, "ev_")
        _identity(self.task_id, "tsk_")
        _identity(self.attempt_id, "att_")
        _choice(self.kind, _EVIDENCE_KINDS)
        _text(self.canonical_evidence_facts, maximum=_CANONICAL_MAX_BYTES)
        _hash(self.content_hash)


@dataclass(frozen=True)
class ResumeHandoffFacts:
    handoff_id: str
    source_attempt_id: str
    target_task_id: str
    result_summary: str
    canonical_handoff_facts: str
    content_hash: str

    def __post_init__(self) -> None:
        _identity(self.handoff_id, "hnd_")
        _identity(self.source_attempt_id, "att_")
        _identity(self.target_task_id, "tsk_")
        _text(self.result_summary)
        _text(self.canonical_handoff_facts, maximum=_CANONICAL_MAX_BYTES)
        _hash(self.content_hash)


@dataclass(frozen=True)
class ResumeStageFacts:
    task_id: str
    task_ordinal: int
    name: str
    role: str
    planned_backend: str
    planned_agent_instance_id: str
    acp_route: str
    task_state: str
    canonical_task_facts: str
    attempts: tuple[ResumeAttemptFacts, ...]
    terminal_command_id: str | None
    terminal_command_hash: str | None
    terminal_attempt_id: str | None
    evidence: tuple[ResumeEvidenceFacts, ...]
    handoff: ResumeHandoffFacts | None

    def __post_init__(self) -> None:
        _identity(self.task_id, "tsk_")
        _integer(self.task_ordinal)
        _text(self.name, maximum=128)
        _choice(self.role, _ROLES)
        _text(self.planned_backend, maximum=255)
        _identity(self.planned_agent_instance_id, "agt_")
        _text(self.acp_route, maximum=255)
        _choice(self.task_state, _TASK_STATES)
        _text(self.canonical_task_facts, maximum=_CANONICAL_MAX_BYTES)
        attempts = _tuple(self.attempts, ResumeAttemptFacts)
        evidence = _tuple(self.evidence, ResumeEvidenceFacts)
        command_group = (
            self.terminal_command_id,
            self.terminal_command_hash,
            self.terminal_attempt_id,
        )
        if all(item is None for item in command_group):
            if evidence or self.handoff is not None:
                _malformed()
        elif any(item is None for item in command_group):
            _malformed()
        else:
            _identity(self.terminal_command_id, "cmd_")
            _hash(self.terminal_command_hash)
            _identity(self.terminal_attempt_id, "att_")
            if not evidence or not attempts:
                _malformed()
        if self.handoff is not None and type(self.handoff) is not ResumeHandoffFacts:
            _malformed()


@dataclass(frozen=True)
class ExecutionResumeFacts:
    session_id: str
    session_state: str
    mission_id: str
    mission_version: int
    mission_content_hash: str
    canonical_mission_facts: str
    stages: tuple[ResumeStageFacts, ...]

    def __post_init__(self) -> None:
        _identity(self.session_id, "ses_")
        if self.session_state != "paused":
            _malformed()
        _identity(self.mission_id, "msn_")
        _integer(self.mission_version)
        _hash(self.mission_content_hash)
        _text(self.canonical_mission_facts, maximum=EXECUTION_RESUME_MAX_BYTES)
        stages = _tuple(self.stages, ResumeStageFacts)
        if len(stages) != 4 or tuple(stage.task_ordinal for stage in stages) != (1, 2, 3, 4):
            _malformed()
        if len({stage.task_id for stage in stages}) != 4:
            _malformed()


def derive_resume_cursor(
    facts: ExecutionResumeFacts,
) -> tuple[int, str | None, int, int | None, str | None]:
    if type(facts) is not ExecutionResumeFacts:
        raise TypeError("resume cursor requires ExecutionResumeFacts")
    closed = 0
    for stage in facts.stages:
        if stage.terminal_command_id is None:
            break
        closed += 1
    if any(stage.terminal_command_id is not None for stage in facts.stages[closed:]):
        _malformed()
    for stage in facts.stages[:closed]:
        if (
            stage.terminal_attempt_id != stage.attempts[-1].attempt_id
            or stage.attempts[-1].state != "completed"
        ):
            _malformed()
    for stage in facts.stages[closed + 1:]:
        if stage.attempts or stage.task_state == "completed":
            _malformed()
    if closed == len(facts.stages):
        return closed, None, 0, None, None
    first = facts.stages[closed]
    if first.task_state == "completed":
        _malformed()
    if first.attempts:
        if any(attempt.state == "outcome_unknown" for attempt in first.attempts):
            raise ExecutionResumeProjectionError(code="resume_outcome_unknown")
        if any(attempt.state != "interrupted" for attempt in first.attempts):
            raise ExecutionResumeProjectionError(code="resume_stage_not_retryable")
        maximum = first.attempts[-1].ordinal
    else:
        maximum = 0
    if maximum == _SQLITE_MAX_INTEGER:
        raise ExecutionResumeProjectionError(code="resume_ordinal_exhausted")
    preceding = None if closed == 0 else facts.stages[closed - 1].handoff
    return (
        closed,
        first.task_id,
        maximum,
        maximum + 1,
        None if preceding is None else preceding.handoff_id,
    )


def _attempt_projection(item: ResumeAttemptFacts) -> dict[str, object]:
    return {name: getattr(item, name) for name in item.__dataclass_fields__}


def _evidence_projection(item: ResumeEvidenceFacts) -> dict[str, object]:
    return {name: getattr(item, name) for name in item.__dataclass_fields__}


def _handoff_projection(item: ResumeHandoffFacts | None) -> dict[str, object] | None:
    if item is None:
        return None
    return {name: getattr(item, name) for name in item.__dataclass_fields__}


def _stage_projection(item: ResumeStageFacts) -> dict[str, object]:
    return {
        "task_id": item.task_id,
        "task_ordinal": item.task_ordinal,
        "name": item.name,
        "role": item.role,
        "planned_backend": item.planned_backend,
        "planned_agent_instance_id": item.planned_agent_instance_id,
        "acp_route": item.acp_route,
        "task_state": item.task_state,
        "canonical_task_facts": item.canonical_task_facts,
        "attempts": [_attempt_projection(attempt) for attempt in item.attempts],
        "terminal_command_id": item.terminal_command_id,
        "terminal_command_hash": item.terminal_command_hash,
        "terminal_attempt_id": item.terminal_attempt_id,
        "evidence": [_evidence_projection(evidence) for evidence in item.evidence],
        "handoff": _handoff_projection(item.handoff),
    }


def _snapshot_projection(
    facts: ExecutionResumeFacts,
    cursor: tuple[int, str | None, int, int | None, str | None],
) -> dict[str, object]:
    return {
        "facts": {
            "session_id": facts.session_id,
            "session_state": facts.session_state,
            "mission_id": facts.mission_id,
            "mission_version": facts.mission_version,
            "mission_content_hash": facts.mission_content_hash,
            "canonical_mission_facts": facts.canonical_mission_facts,
            "stages": [_stage_projection(stage) for stage in facts.stages],
        },
        "closed_stage_count": cursor[0],
        "first_unclosed_task_id": cursor[1],
        "max_prior_attempt_ordinal": cursor[2],
        "next_attempt_ordinal": cursor[3],
        "preceding_handoff_id": cursor[4],
    }


@dataclass(frozen=True)
class ExecutionResumeSnapshot:
    facts: ExecutionResumeFacts
    closed_stage_count: int
    first_unclosed_task_id: str | None
    max_prior_attempt_ordinal: int
    next_attempt_ordinal: int | None
    preceding_handoff_id: str | None
    content_hash: str

    def __post_init__(self) -> None:
        if type(self.facts) is not ExecutionResumeFacts:
            _malformed()
        _integer(self.closed_stage_count, allow_zero=True)
        _optional_identity(self.first_unclosed_task_id, "tsk_")
        _integer(self.max_prior_attempt_ordinal, allow_zero=True)
        if self.next_attempt_ordinal is not None:
            _integer(self.next_attempt_ordinal)
        _optional_identity(self.preceding_handoff_id, "hnd_")
        _hash(self.content_hash)
        self.validate_hash()

    @classmethod
    def create(cls, facts: ExecutionResumeFacts) -> "ExecutionResumeSnapshot":
        cursor = derive_resume_cursor(facts)
        encoded = _canonical_bytes(_snapshot_projection(facts, cursor))
        return cls(facts, *cursor, sha256(encoded).hexdigest())

    def canonical_facts(self) -> dict[str, object]:
        cursor = (
            self.closed_stage_count,
            self.first_unclosed_task_id,
            self.max_prior_attempt_ordinal,
            self.next_attempt_ordinal,
            self.preceding_handoff_id,
        )
        return _snapshot_projection(self.facts, cursor)

    def validate_hash(self) -> None:
        derived = derive_resume_cursor(self.facts)
        supplied = (
            self.closed_stage_count,
            self.first_unclosed_task_id,
            self.max_prior_attempt_ordinal,
            self.next_attempt_ordinal,
            self.preceding_handoff_id,
        )
        if supplied != derived:
            _malformed()
        encoded = _canonical_bytes(self.canonical_facts())
        if not compare_digest(sha256(encoded).hexdigest(), self.content_hash):
            _malformed()


def _canonical_bytes(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeEncodeError):
        _malformed()
    if not encoded or len(encoded) > EXECUTION_RESUME_MAX_BYTES:
        _malformed()
    return encoded


__all__ = [
    "EXECUTION_RESUME_MAX_BYTES",
    "ExecutionResumeFacts",
    "ExecutionResumeProjectionError",
    "ExecutionResumeSnapshot",
    "ResumeAttemptFacts",
    "ResumeEvidenceFacts",
    "ResumeHandoffFacts",
    "ResumeStageFacts",
]

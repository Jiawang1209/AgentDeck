"""Pure execution lineage facts for Attempts, evidence, and handoffs."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256
import json

class ResultError(ValueError):
    """Raised when execution result lineage is incomplete or contradictory."""

class AttemptState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    HUMAN_CONTROLLED = "human_controlled"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    OUTCOME_UNKNOWN = "outcome_unknown"

class EvidenceKind(StrEnum):
    TEST_EXIT_STATUS = "test_exit_status"
    DIFF_IDENTITY = "diff_identity"
    ARTIFACT_HASH = "artifact_hash"
    REVIEW_FINDING = "review_finding"
    ACCEPTANCE_RESULT = "acceptance_result"
    HUMAN_DECISION = "human_decision"

class FindingSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    BLOCKING = "blocking"

_SQLITE_MIN_INTEGER = -(2**63)
_SQLITE_MAX_INTEGER = 2**63 - 1
_LOWER_HEX = frozenset("0123456789abcdef")
_KNOWN_RETRYABLE_FAILURES = frozenset({"worker_failed", "worker_schema_invalid", "recoverable_transport_interruption"})
_TERMINAL_ATTEMPT_STATES = frozenset({AttemptState.COMPLETED, AttemptState.FAILED, AttemptState.CANCELLED, AttemptState.INTERRUPTED, AttemptState.OUTCOME_UNKNOWN})
_LEGAL_ATTEMPT_TRANSITIONS = {
    AttemptState.PENDING: frozenset({
        AttemptState.RUNNING, AttemptState.FAILED, AttemptState.CANCELLED,
    }),
    AttemptState.RUNNING: frozenset({
        AttemptState.AWAITING_APPROVAL, AttemptState.HUMAN_CONTROLLED,
        AttemptState.COMPLETED, AttemptState.FAILED, AttemptState.CANCELLED,
        AttemptState.INTERRUPTED, AttemptState.OUTCOME_UNKNOWN,
    }),
    AttemptState.AWAITING_APPROVAL: frozenset({
        AttemptState.RUNNING, AttemptState.HUMAN_CONTROLLED, AttemptState.FAILED,
        AttemptState.CANCELLED, AttemptState.INTERRUPTED,
        AttemptState.OUTCOME_UNKNOWN,
    }),
    AttemptState.HUMAN_CONTROLLED: frozenset({
        AttemptState.RUNNING, AttemptState.COMPLETED, AttemptState.FAILED,
        AttemptState.CANCELLED, AttemptState.INTERRUPTED,
        AttemptState.OUTCOME_UNKNOWN,
    }),
    **{state: frozenset() for state in _TERMINAL_ATTEMPT_STATES},
}

def _require_string(value: object, field: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{field} must be a string")
    try:
        value.encode("utf-8", "strict")
    except UnicodeEncodeError as error:
        raise ValueError(f"{field} must be valid UTF-8") from error
    if not value.strip() or len(value.encode("utf-8")) > 64 * 1024:
        raise ValueError(f"{field} must be nonempty and bounded")
    return value

def _require_id(value: object, prefix: str, field: str) -> str:
    text = _require_string(value, field)
    if not text.startswith(prefix) or not text.removeprefix(prefix).strip():
        raise ValueError(f"{field} must start with {prefix} and have a suffix")
    if any(character.isspace() for character in text):
        raise ValueError(f"{field} must not contain whitespace")
    return text

def _sqlite_integer(value: object, field: str, *, positive: bool = False) -> int:
    if type(value) is not int:
        raise TypeError(f"{field} must be an int")
    if positive and not 1 <= value <= _SQLITE_MAX_INTEGER:
        raise ValueError(f"{field} must be a positive SQLite-safe integer")
    if not positive and not _SQLITE_MIN_INTEGER <= value <= _SQLITE_MAX_INTEGER:
        raise ValueError(f"{field} must be a SQLite-safe integer")
    return value

def _copy_strings(
    values: object, field: str, *, nonempty: bool, id_prefix: str | None = None
) -> tuple[str, ...]:
    if type(values) not in {list, tuple}:
        raise TypeError(f"{field} must be a list or tuple")
    try:
        copied = tuple(
            _require_id(value, id_prefix, f"{field} items")
            if id_prefix is not None
            else _require_string(value, f"{field} items")
            for value in values
        )
    except (TypeError, ValueError) as error:
        if id_prefix is not None:
            raise ResultError(f"{field} must contain typed evidence IDs") from error
        raise
    if nonempty and not copied:
        raise ResultError(f"{field} must contain typed evidence")
    if len(copied) != len(set(copied)):
        raise ResultError(f"{field} must not contain duplicate IDs")
    return copied

def _canonical_json(value: object) -> str:
    content = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    try:
        content.encode("utf-8", "strict")
    except UnicodeEncodeError as error:
        raise ValueError("canonical content must be valid UTF-8") from error
    return content

def _require_hash(value: object, field: str) -> str:
    text = _require_string(value, field)
    if len(text) != 64 or any(character not in _LOWER_HEX for character in text):
        raise ValueError(f"{field} must be exactly 64 lowercase hex characters")
    return text

@dataclass(frozen=True)
class Attempt:
    attempt_id: str
    task_id: str
    ordinal: int
    state: AttemptState
    reason: str | None = None
    result_summary: str | None = None
    retryable: bool = False

    def __post_init__(self) -> None:
        _require_id(self.attempt_id, "att_", "attempt_id")
        _require_id(self.task_id, "tsk_", "task_id")
        _sqlite_integer(self.ordinal, "ordinal", positive=True)
        if type(self.state) is not AttemptState:
            raise TypeError("state must be an AttemptState")
        if type(self.retryable) is not bool:
            raise TypeError("retryable must be a bool")
        if self.reason is not None:
            _require_string(self.reason, "reason")
        if self.result_summary is not None:
            _require_string(self.result_summary, "result_summary")
        if self.retryable and self.reason not in _KNOWN_RETRYABLE_FAILURES:
            raise ResultError("prohibited failure reason is not retryable")
        if self.state is AttemptState.COMPLETED:
            if self.result_summary is None or self.reason is not None or self.retryable:
                raise ResultError("completed attempt requires only a result summary")
        elif self.state in {
            AttemptState.FAILED,
            AttemptState.CANCELLED,
            AttemptState.INTERRUPTED,
            AttemptState.OUTCOME_UNKNOWN,
        }:
            if self.reason is None or self.result_summary is not None:
                raise ResultError(f"{self.state.value} attempt requires only a reason")
            if self.state is not AttemptState.FAILED and self.retryable:
                raise ResultError("only a known failed attempt can be retryable")
        elif self.reason is not None or self.result_summary is not None or self.retryable:
            raise ResultError("active attempt cannot carry terminal result fields")

    @classmethod
    def pending(cls, attempt_id: str, task_id: str, ordinal: int) -> "Attempt":
        return cls(attempt_id, task_id, ordinal, AttemptState.PENDING)

    def transition(
        self,
        state: AttemptState,
        *,
        reason: str | None = None,
        result_summary: str | None = None,
        retryable: bool = False,
    ) -> "Attempt":
        if type(state) is not AttemptState:
            raise TypeError("state must be an AttemptState")
        if self.state in _TERMINAL_ATTEMPT_STATES:
            raise ResultError("terminal attempt cannot transition")
        if state not in _LEGAL_ATTEMPT_TRANSITIONS[self.state]:
            raise ResultError(
                f"illegal attempt transition: {self.state.value} -> {state.value}"
            )
        return replace(self, state=state, reason=reason,
                       result_summary=result_summary, retryable=retryable)

    def start(self) -> "Attempt":
        return self.transition(AttemptState.RUNNING)
    def await_approval(self) -> "Attempt":
        return self.transition(AttemptState.AWAITING_APPROVAL)
    def take_human_control(self) -> "Attempt":
        return self.transition(AttemptState.HUMAN_CONTROLLED)
    def resume(self) -> "Attempt":
        return self.transition(AttemptState.RUNNING)
    def complete(self, result_summary: str) -> "Attempt":
        return self.transition(AttemptState.COMPLETED, result_summary=result_summary)
    def fail(self, reason: str, *, retryable: bool | None = None) -> "Attempt":
        if retryable is None:
            retryable = reason in _KNOWN_RETRYABLE_FAILURES
        return self.transition(AttemptState.FAILED, reason=reason, retryable=retryable)
    def cancel(self, reason: str) -> "Attempt":
        return self.transition(AttemptState.CANCELLED, reason=reason)
    def interrupt(self, reason: str) -> "Attempt":
        return self.transition(AttemptState.INTERRUPTED, reason=reason)
    def unknown_outcome(self, reason: str) -> "Attempt":
        return self.transition(AttemptState.OUTCOME_UNKNOWN, reason=reason)

    def retry(self, new_id: str) -> "Attempt":
        if self.state is not AttemptState.FAILED or not self.retryable:
            raise ResultError("attempt is not explicitly retryable")
        checked_id = _require_id(new_id, "att_", "new attempt_id")
        if checked_id == self.attempt_id:
            raise ResultError("retry requires a new attempt_id")
        try:
            ordinal = _sqlite_integer(self.ordinal + 1, "ordinal", positive=True)
        except ValueError as error:
            raise ResultError("retry ordinal exceeds SQLite-safe range") from error
        return Attempt.pending(checked_id, self.task_id, ordinal)

@dataclass(frozen=True)
class ReviewFinding:
    finding_id: str
    scope: str
    severity: FindingSeverity
    summary: str
    criterion: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_id(self.finding_id, "rfn_", "finding_id")
        _require_string(self.scope, "scope")
        if type(self.severity) is not FindingSeverity:
            raise TypeError("severity must be a FindingSeverity")
        _require_string(self.summary, "summary")
        _require_string(self.criterion, "criterion")
        evidence = _copy_strings(
            self.evidence_ids, "evidence_ids", nonempty=True, id_prefix="ev_"
        )
        object.__setattr__(self, "evidence_ids", evidence)

    def canonical_projection(self) -> dict[str, object]:
        return {
            "finding_id": self.finding_id,
            "scope": self.scope,
            "severity": self.severity.value,
            "summary": self.summary,
            "criterion": self.criterion,
            "evidence_ids": list(self.evidence_ids),
        }
@dataclass(frozen=True)
class AcceptanceResult:
    criteria: tuple[str, ...]
    evidence_by_criterion: tuple[tuple[str, tuple[str, ...]], ...]
    accepted: bool
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        criteria = _copy_strings(self.criteria, "criteria", nonempty=True)
        if type(self.accepted) is not bool:
            raise TypeError("accepted must be a bool")
        entries = self.evidence_by_criterion
        if isinstance(entries, Mapping):
            raw_entries = tuple(entries.items())
        elif type(entries) in {list, tuple}:
            raw_entries = tuple(entries)
        else:
            raise TypeError("evidence mapping must be a mapping or entries")
        normalized: dict[str, tuple[str, ...]] = {}
        for entry in raw_entries:
            if type(entry) not in {list, tuple} or len(entry) != 2:
                raise TypeError("evidence entries must be criterion and IDs pairs")
            criterion = _require_string(entry[0], "evidence criterion")
            if criterion in normalized:
                raise ResultError("evidence mapping must not duplicate criteria")
            normalized[criterion] = _copy_strings(
                entry[1], f"evidence for {criterion}", nonempty=True,
                id_prefix="ev_",
            )
        if set(normalized) != set(criteria):
            raise ResultError("evidence mapping must cover exactly every criterion")
        if self.accepted and self.failure_reason is not None:
            raise ResultError("accepted result cannot carry a failure_reason")
        if not self.accepted and self.failure_reason is None:
            raise ResultError("failed acceptance requires a failure_reason")
        if self.failure_reason is not None:
            _require_string(self.failure_reason, "failure_reason")
        object.__setattr__(self, "criteria", criteria)
        ordered = tuple((criterion, normalized[criterion]) for criterion in criteria)
        object.__setattr__(self, "evidence_by_criterion", ordered)

    @classmethod
    def create(
        cls,
        criteria: object,
        evidence_mapping: object,
        *,
        accepted: bool,
        failure_reason: str | None = None,
    ) -> "AcceptanceResult":
        return cls(criteria, evidence_mapping, accepted, failure_reason)  # type: ignore[arg-type]

    def canonical_projection(self) -> dict[str, object]:
        return {
            "criteria": list(self.criteria),
            "evidence_by_criterion": {
                criterion: list(evidence_ids)
                for criterion, evidence_ids in self.evidence_by_criterion
            },
            "accepted": self.accepted,
            "failure_reason": self.failure_reason,
        }
_EVIDENCE_FIELDS = {
    EvidenceKind.TEST_EXIT_STATUS: {"command", "exit_status"},
    EvidenceKind.DIFF_IDENTITY: {"base", "head", "diff_hash"},
    EvidenceKind.ARTIFACT_HASH: {"artifact_reference", "content_hash"},
    EvidenceKind.REVIEW_FINDING: {
        "finding_id", "scope", "severity", "summary", "criterion", "evidence_ids"
    },
    EvidenceKind.ACCEPTANCE_RESULT: {
        "criteria", "evidence_by_criterion", "accepted", "failure_reason"
    },
    EvidenceKind.HUMAN_DECISION: {"actor", "decision"},
}

def _validate_evidence_payload(kind: EvidenceKind, payload: object) -> None:
    if type(payload) is not dict or set(payload) != _EVIDENCE_FIELDS[kind]:
        raise ResultError(f"{kind.value} evidence requires an exact typed payload")
    if kind is EvidenceKind.TEST_EXIT_STATUS:
        _require_string(payload["command"], "command")
        _sqlite_integer(payload["exit_status"], "exit_status")
    elif kind is EvidenceKind.DIFF_IDENTITY:
        _require_string(payload["base"], "base")
        _require_string(payload["head"], "head")
        _require_hash(payload["diff_hash"], "diff_hash")
    elif kind is EvidenceKind.ARTIFACT_HASH:
        _require_string(payload["artifact_reference"], "artifact_reference")
        _require_hash(payload["content_hash"], "content_hash")
    elif kind is EvidenceKind.REVIEW_FINDING:
        ReviewFinding(
            payload["finding_id"], payload["scope"],
            FindingSeverity(payload["severity"]), payload["summary"],
            payload["criterion"], payload["evidence_ids"],
        )
    elif kind is EvidenceKind.ACCEPTANCE_RESULT:
        AcceptanceResult.create(
            payload["criteria"], payload["evidence_by_criterion"],
            accepted=payload["accepted"], failure_reason=payload["failure_reason"],
        )
    else:
        _require_string(payload["actor"], "actor")
        _require_string(payload["decision"], "decision")
def _references_evidence_id(kind: EvidenceKind, payload: dict[str, object], evidence_id: str) -> bool:
    if kind is EvidenceKind.REVIEW_FINDING:
        return evidence_id in payload["evidence_ids"]
    return kind is EvidenceKind.ACCEPTANCE_RESULT and any(
        evidence_id in ids
        for ids in payload["evidence_by_criterion"].values()
    )
@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    kind: EvidenceKind
    payload: str
    def __post_init__(self) -> None:
        _require_id(self.evidence_id, "ev_", "evidence_id")
        if type(self.kind) is not EvidenceKind:
            raise TypeError("kind must be an EvidenceKind")
        _require_string(self.payload, "payload")
        try:
            plain = json.loads(self.payload)
        except json.JSONDecodeError as error:
            raise ResultError("evidence payload must be canonical JSON") from error
        if self.payload != _canonical_json(plain):
            raise ResultError("canonical evidence content does not match payload")
        _validate_evidence_payload(self.kind, plain)
        if _references_evidence_id(self.kind, plain, self.evidence_id):
            raise ResultError("evidence self-reference is not allowed")

    @property
    def canonical_content(self) -> str:
        return self.payload

    @classmethod
    def create(
        cls, evidence_id: str, kind: EvidenceKind, payload: object
    ) -> "Evidence":
        if type(kind) is not EvidenceKind:
            raise TypeError("kind must be an EvidenceKind")
        if not isinstance(payload, Mapping):
            raise TypeError("evidence payload must be a mapping")
        if any(type(key) is not str for key in payload):
            raise TypeError("evidence payload object keys must be strings")
        raw = dict(payload)
        _validate_evidence_payload(kind, raw)
        return cls(evidence_id, kind, _canonical_json(raw))

    @classmethod
    def acceptance(
        cls,
        evidence_id: str,
        *,
        result: AcceptanceResult | None = None,
        source_kind: EvidenceKind | str,
    ) -> "Evidence":
        if source_kind is not EvidenceKind.ACCEPTANCE_RESULT:
            raise ResultError("acceptance requires typed evidence, not worker prose")
        if type(result) is not AcceptanceResult:
            raise ResultError("acceptance requires a typed evidence result")
        payload = result.canonical_projection()
        return cls.create(evidence_id, EvidenceKind.ACCEPTANCE_RESULT, payload)
@dataclass(frozen=True)
class Handoff:
    handoff_id: str
    source_attempt_id: str
    target_task_id: str
    result_summary: str
    verification_evidence_ids: tuple[str, ...]
    artifact_references: tuple[str, ...]
    known_issues: tuple[str, ...]
    canonical_content: str
    content_hash: str

    def __post_init__(self) -> None:
        _require_id(self.handoff_id, "hnd_", "handoff_id")
        _require_id(self.source_attempt_id, "att_", "source_attempt_id")
        _require_id(self.target_task_id, "tsk_", "target_task_id")
        if self.source_attempt_id == self.target_task_id:
            raise ResultError("handoff source and target identities must differ")
        _require_string(self.result_summary, "result_summary")
        evidence_ids = _copy_strings(
            self.verification_evidence_ids, "verification_evidence_ids",
            nonempty=True, id_prefix="ev_",
        )
        artifacts = _copy_strings(
            self.artifact_references, "artifact_references", nonempty=False
        )
        issues = _copy_strings(self.known_issues, "known_issues", nonempty=False)
        object.__setattr__(self, "verification_evidence_ids", evidence_ids)
        object.__setattr__(self, "artifact_references", artifacts)
        object.__setattr__(self, "known_issues", issues)
        canonical = _canonical_json(self.canonical_projection())
        _require_string(self.canonical_content, "canonical_content")
        if self.canonical_content != canonical:
            raise ResultError("canonical handoff content does not match projection")
        checked_hash = _require_hash(self.content_hash, "content_hash")
        if checked_hash != sha256(canonical.encode("utf-8")).hexdigest():
            raise ResultError("content_hash does not match canonical handoff content")

    @classmethod
    def create(
        cls,
        handoff_id: str,
        source_attempt_id: str,
        target_task_id: str,
        result_summary: str,
        verification_evidence_ids: object,
        *,
        artifact_references: object = (),
        known_issues: object = (),
    ) -> "Handoff":
        evidence_ids = _copy_strings(
            verification_evidence_ids, "verification_evidence_ids",
            nonempty=True, id_prefix="ev_",
        )
        artifacts = _copy_strings(
            artifact_references, "artifact_references", nonempty=False
        )
        issues = _copy_strings(known_issues, "known_issues", nonempty=False)
        projection = {
            "handoff_id": handoff_id,
            "source_attempt_id": source_attempt_id,
            "target_task_id": target_task_id,
            "result_summary": result_summary,
            "artifact_references": list(artifacts),
            "verification_evidence_ids": list(evidence_ids),
            "known_issues": list(issues),
        }
        content = _canonical_json(projection)
        return cls(
            handoff_id, source_attempt_id, target_task_id, result_summary,
            evidence_ids, artifacts, issues, content,
            sha256(content.encode("utf-8")).hexdigest(),
        )

    def canonical_projection(self) -> dict[str, object]:
        return {
            "handoff_id": self.handoff_id,
            "source_attempt_id": self.source_attempt_id,
            "target_task_id": self.target_task_id,
            "result_summary": self.result_summary,
            "artifact_references": list(self.artifact_references),
            "verification_evidence_ids": list(self.verification_evidence_ids),
            "known_issues": list(self.known_issues),
        }

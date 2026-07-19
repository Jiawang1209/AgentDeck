"""Pure execution request, result, identity, and snapshot boundaries."""
from dataclasses import dataclass
from hashlib import sha256
import json
from types import MappingProxyType
from agentdeck.kernel.diagnostics import Diagnostic
from agentdeck.kernel.execution import (
    Attempt,
    AttemptState,
    Evidence,
    EvidenceKind,
    Handoff,
    ResultError,
    ReviewFinding,
)
from agentdeck.kernel.execution_semantics import (
    ReviewResult,
    RevisionResult,
    validate_acceptance,
)
from agentdeck.kernel.mission import (
    ConfirmedMissionVersion,
    MissionDraft,
    TaskDefinition,
)
from agentdeck.ports.worker import TaskRequest, WorkerHandle, WorkerResult
_TERMINAL_RESULT_FIELDS = {
    "mission_id", "mission_version", "task_id", "attempt_id",
    "evidence_ids", "handoff_id",
}
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
def evidence_snapshot(evidence: Evidence, attempt: Attempt) -> dict[str, object]:
    return {
        "evidence_id": evidence.evidence_id, "task_id": attempt.task_id,
        "attempt_id": attempt.attempt_id, "kind": evidence.kind.value,
        "canonical_evidence_facts": evidence.canonical_content,
    }
def handoff_snapshot(handoff: Handoff) -> dict[str, object]:
    return {
        "handoff_id": handoff.handoff_id,
        "source_attempt_id": handoff.source_attempt_id,
        "target_task_id": handoff.target_task_id,
        "result_summary": handoff.result_summary,
        "canonical_handoff_facts": handoff.canonical_content,
        "content_hash": handoff.content_hash,
    }


def terminal_command_result(
    confirmed: ConfirmedMissionVersion, task: TaskDefinition, attempt: Attempt,
    evidence: tuple[Evidence, ...], handoff: Handoff | None,
) -> dict[str, object]:
    return {
        "mission_id": confirmed.mission_id, "mission_version": confirmed.version,
        "task_id": task.task_id, "attempt_id": attempt.attempt_id,
        "evidence_ids": [item.evidence_id for item in evidence],
        "handoff_id": None if handoff is None else handoff.handoff_id,
    }


def terminal_references(result: object) -> tuple[str, tuple[str, ...], str | None]:
    if type(result) is not dict or set(result) != _TERMINAL_RESULT_FIELDS:
        raise ValueError("terminal execution bundle is invalid")
    attempt_id, evidence_ids, handoff_id = (
        result["attempt_id"], result["evidence_ids"], result["handoff_id"]
    )
    if (type(attempt_id) is not str or type(evidence_ids) is not list
            or not evidence_ids or any(type(item) is not str for item in evidence_ids)
            or len(evidence_ids) != len(set(evidence_ids))
            or (handoff_id is not None and type(handoff_id) is not str)):
        raise ValueError("terminal execution bundle is invalid")
    return attempt_id, tuple(evidence_ids), handoff_id


@dataclass(frozen=True)
class CommittedTerminalBundle:
    attempt: Attempt
    evidence: tuple[Evidence, ...]
    handoff: Handoff | None


def validated_terminal_bundle(
    result: object, confirmed: ConfirmedMissionVersion, task: TaskDefinition,
    expected_attempt: Attempt, expected_evidence: tuple[Evidence, ...],
    expected_handoff: Handoff | None, expected_acp_session_id: str,
    attempt_facts: object,
    evidence_facts: tuple[object, ...], handoff_facts: object,
) -> CommittedTerminalBundle:
    expected_result = terminal_command_result(
        confirmed, task, expected_attempt, expected_evidence, expected_handoff
    )
    expected_facts = (
        attempt_snapshot(expected_attempt, task, expected_acp_session_id),
        tuple(evidence_snapshot(item, expected_attempt) for item in expected_evidence),
        None if expected_handoff is None else handoff_snapshot(expected_handoff),
    )
    actual_facts = (attempt_facts, evidence_facts, handoff_facts)
    if any(type(value) is not dict for value in (result, attempt_facts)) or (
        json.dumps(result, sort_keys=True) != json.dumps(expected_result, sort_keys=True)
        or len(evidence_facts) != len(expected_evidence)
        or json.dumps(actual_facts, sort_keys=True)
        != json.dumps(expected_facts, sort_keys=True)
    ):
        raise ValueError("terminal execution bundle is invalid")
    loaded_attempt = Attempt(
        attempt_facts["attempt_id"], attempt_facts["task_id"],
        attempt_facts["ordinal"], AttemptState(attempt_facts["state"]),
        attempt_facts["reason"], attempt_facts["result_summary"],
        attempt_facts["retryable"],
    )
    loaded_evidence = tuple(Evidence(facts["evidence_id"],
        EvidenceKind(facts["kind"]), facts["canonical_evidence_facts"])
        for facts in evidence_facts)
    loaded_handoff = None
    if handoff_facts is not None:
        facts = json.loads(handoff_facts["canonical_handoff_facts"])
        loaded_handoff = Handoff.create(
            facts["handoff_id"], facts["source_attempt_id"], facts["target_task_id"],
            facts["result_summary"], facts["verification_evidence_ids"],
            artifact_references=facts["artifact_references"],
            known_issues=facts["known_issues"],
        )
    return CommittedTerminalBundle(loaded_attempt, loaded_evidence, loaded_handoff)


def handle_matches_request(handle: object, request: TaskRequest) -> bool:
    return type(handle) is WorkerHandle and (
        handle.agent_id, handle.task_id, handle.attempt_id, handle.transport
    ) == (request.agent_id, request.task_id, request.attempt_id, "acp")


def _derived_id(prefix: str, *parts: str) -> str:
    canonical = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return prefix + sha256(canonical.encode("utf-8")).hexdigest()[:24]


def stage_id(
    prefix: str,
    confirmed: ConfirmedMissionVersion,
    task: TaskDefinition,
    *parts: str,
) -> str:
    return _derived_id(
        prefix, confirmed.mission_id, str(confirmed.version), task.task_id, *parts
    )


def command_id(
    phase: str,
    confirmed: ConfirmedMissionVersion,
    task: TaskDefinition,
    ordinal: int,
) -> str:
    return stage_id("cmd_", confirmed, task, phase, str(ordinal))


@dataclass(frozen=True)
class AuthoritativeRevisionFinding:
    finding: ReviewFinding
    review_evidence: Evidence

    def __post_init__(self) -> None:
        if type(self.finding) is not ReviewFinding:
            raise TypeError("authoritative finding requires a ReviewFinding")
        if (
            type(self.review_evidence) is not Evidence
            or self.review_evidence.kind is not EvidenceKind.REVIEW_FINDING
            or json.loads(self.review_evidence.canonical_content)
            != self.finding.canonical_projection()
        ):
            raise ResultError("authoritative finding evidence does not match")

    def canonical_payload(self) -> dict[str, object]:
        finding = self.finding
        return {
            "finding_id": finding.finding_id, "scope": finding.scope,
            "severity": finding.severity.value, "summary": finding.summary,
            "criterion": finding.criterion,
            "evidence_lineage": {
                "review_evidence_id": self.review_evidence.evidence_id,
                "reference_evidence_ids": list(finding.evidence_ids),
            },
        }


@dataclass(frozen=True)
class AuthoritativeRevisionTask:
    task_id: str
    created_by: str
    confirmed_scope: str
    accepted_finding_ids: tuple[str, ...]
    accepted_findings: tuple[AuthoritativeRevisionFinding, ...] = ()

    def __post_init__(self) -> None:
        findings = tuple(self.accepted_findings)
        if any(type(item) is not AuthoritativeRevisionFinding for item in findings):
            raise TypeError("accepted findings must be authoritative projections")
        if tuple(item.finding.finding_id for item in findings) != tuple(
            self.accepted_finding_ids
        ):
            raise ResultError("accepted finding content must exactly match its IDs")
        object.__setattr__(self, "accepted_finding_ids", tuple(self.accepted_finding_ids))
        object.__setattr__(self, "accepted_findings", findings)

    @classmethod
    def from_review(
        cls, task_id: str, confirmed_scope: str, findings: tuple[ReviewFinding, ...],
        evidence: tuple[Evidence, ...],
    ) -> "AuthoritativeRevisionTask":
        review_evidence = {
            json.loads(item.canonical_content)["finding_id"]: item
            for item in evidence if item.kind is EvidenceKind.REVIEW_FINDING
        }
        accepted = tuple(
            AuthoritativeRevisionFinding(finding, review_evidence[finding.finding_id])
            for finding in findings
        )
        return cls(
            task_id, "agentdeck", confirmed_scope,
            tuple(finding.finding_id for finding in findings), accepted,
        )

    def canonical_payload(self) -> dict[str, object]:
        return {
            "task_id": self.task_id, "created_by": self.created_by,
            "confirmed_scope": self.confirmed_scope,
            "accepted_finding_ids": list(self.accepted_finding_ids),
            "accepted_findings": [
                finding.canonical_payload() for finding in self.accepted_findings
            ],
        }

    @property
    def review_evidence_ids(self) -> tuple[str, ...]:
        return tuple(
            item.review_evidence.evidence_id for item in self.accepted_findings
        )


def task_instruction(
    draft: MissionDraft,
    confirmed: ConfirmedMissionVersion,
    task: TaskDefinition,
    attempt: Attempt,
    incoming_handoff: Handoff | None,
    revision_payload: dict[str, object] | None,
) -> str:
    payload = {
        "mission": {
            "mission_id": confirmed.mission_id,
            "version": confirmed.version,
            "content_hash": confirmed.content_hash,
            "objective": draft.objective,
            "scope": draft.scope,
        },
        "task": task.canonical_projection(),
        "attempt": {
            "attempt_id": attempt.attempt_id,
            "ordinal": attempt.ordinal,
        },
        "incoming_handoff": (
            None if incoming_handoff is None else {
                "canonical_content": incoming_handoff.canonical_content,
                "content_hash": incoming_handoff.content_hash,
            }
        ),
        "authoritative_revision_task": revision_payload,
    }
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def payload_value(result: WorkerResult, field: str) -> object:
    if field not in result.payload:
        raise ValueError(f"worker result missing {field}")
    return result.payload[field]


def payload_text(result: WorkerResult, field: str) -> str:
    value = payload_value(result, field)
    if type(value) is not str or not value.strip():
        raise ValueError(f"worker result {field} must be text")
    return value


def _plain(value: object) -> object:
    if type(value) is MappingProxyType:
        return {key: _plain(child) for key, child in value.items()}
    if type(value) is tuple:
        return [_plain(child) for child in value]
    return value


def _plain_payload(result: WorkerResult) -> dict[str, object]:
    return {key: _plain(value) for key, value in result.payload.items()}


@dataclass(frozen=True)
class CommittedEvidence:
    evidence: Evidence
    mission_id: str
    task_id: str
    attempt_id: str


class EvidenceLineageError(ValueError):
    """Raised without worker content when evidence authority does not resolve."""


@dataclass(frozen=True)
class EvidenceAuthority:
    committed: tuple[CommittedEvidence, ...]
    mission_id: str
    source_task_id: str
    attempts: tuple[Attempt, ...]

    def validate(self, reference_ids: tuple[str, ...]) -> None:
        validate_evidence_lineage(
            reference_ids, self.committed, mission_id=self.mission_id,
            source_task_id=self.source_task_id,
            source_attempt_ids=tuple(
                item.attempt_id for item in self.attempts
                if item.task_id == self.source_task_id and item.state.value == "completed"
            ),
        )


@dataclass(frozen=True)
class ValidatedStageResult:
    summary: str
    evidence: tuple[Evidence, ...]
    artifact_references: tuple[str, ...]
    reference_ids: tuple[str, ...]
    review_findings: tuple[ReviewFinding, ...] = ()
    accepted: bool = True


def review_result(result: WorkerResult) -> ReviewResult:
    return ReviewResult.from_mapping(_plain_payload(result))


def validated_stage_result(
    identity: str, task: TaskDefinition, result: WorkerResult, draft: MissionDraft,
    *, accepted_finding_ids: tuple[str, ...],
    expected_revision_evidence_ids: tuple[str, ...],
    evidence_authority: EvidenceAuthority | None = None,
) -> ValidatedStageResult:
    if task.name == "implementation":
        artifact = payload_text(result, "artifact_reference")
        evidence = Evidence.create(identity, EvidenceKind.ARTIFACT_HASH, {
            "artifact_reference": artifact,
            "content_hash": payload_text(result, "content_hash"),
        })
        return ValidatedStageResult(
            payload_text(result, "summary"), (evidence,), (artifact,), (),
        )
    if task.name == "review":
        review = review_result(result)
        references = tuple(
            evidence_id for finding in review.findings
            for evidence_id in finding.evidence_ids
        )
        if evidence_authority is not None:
            evidence_authority.validate(references)
        evidence = tuple(
            Evidence.create(
                _derived_id("ev_", identity, finding.finding_id),
                EvidenceKind.REVIEW_FINDING, finding.canonical_projection(),
            )
            for finding in review.findings
        )
        return ValidatedStageResult(
            review.summary, evidence, (), references, review.findings,
        )
    if task.name == "revision":
        revision = RevisionResult.from_mapping(
            _plain_payload(result), accepted_finding_ids=accepted_finding_ids,
            expected_evidence_ids=expected_revision_evidence_ids,
        )
        if evidence_authority is not None:
            evidence_authority.validate(revision.evidence_ids)
        evidence = Evidence.create(identity, EvidenceKind.DIFF_IDENTITY, {
            "base": revision.base, "head": revision.head,
            "diff_hash": revision.diff_hash,
        })
        return ValidatedStageResult(
            revision.summary, (evidence,), (), revision.evidence_ids,
        )
    payload = _plain_payload(result)
    if set(payload) != {
        "summary", "criteria", "evidence_by_criterion", "accepted", "failure_reason"
    } or payload["criteria"] != list(draft.acceptance_criteria):
        raise ValueError("acceptance result requires an exact typed payload")
    acceptance = validate_acceptance(
        criteria=draft.acceptance_criteria,
        mappings=payload["evidence_by_criterion"],
        accepted=payload["accepted"], failure_reason=payload["failure_reason"],
    )
    references = tuple(
        evidence_id for _, evidence_ids in acceptance.evidence_by_criterion
        for evidence_id in evidence_ids
    )
    if evidence_authority is not None:
        evidence_authority.validate(references)
    evidence = Evidence.acceptance(
        identity, result=acceptance, source_kind=EvidenceKind.ACCEPTANCE_RESULT
    )
    return ValidatedStageResult(
        payload_text(result, "summary"), (evidence,), (), references,
        accepted=acceptance.accepted,
    )


def validate_evidence_lineage(
    reference_ids: tuple[str, ...], committed: tuple[CommittedEvidence, ...],
    *, mission_id: str, source_task_id: str, source_attempt_ids: tuple[str, ...],
) -> None:
    indexed = {item.evidence.evidence_id: item for item in committed}
    if any(
        reference not in indexed
        or indexed[reference].mission_id != mission_id
        or indexed[reference].task_id != source_task_id
        or indexed[reference].attempt_id not in source_attempt_ids
        for reference in reference_ids
    ):
        raise EvidenceLineageError("evidence lineage is invalid")


def exception_condition(
    error: BaseException, *, task_id: str, attempt_id: str,
) -> str | None:
    try:
        diagnostic = error.diagnostic  # type: ignore[attr-defined]
    except Exception:
        return None
    if type(diagnostic) is not Diagnostic:
        return None
    if diagnostic.task_id != task_id or diagnostic.attempt_id != attempt_id:
        return None
    if (
        diagnostic.code == "acp_disconnected_before_effect"
        and diagnostic.retryable is True
        and diagnostic.outcome_known is True
    ):
        return "transport_before_effect"
    if (
        diagnostic.code == "worker_schema_invalid"
        and diagnostic.retryable is True
        and diagnostic.outcome_known is True
    ):
        return "worker_schema_invalid"
    if diagnostic.outcome_known is False:
        return "outcome_unknown"
    aliases = {
        "login_loss": "login_loss",
        "worker_login_lost": "login_loss",
        "project_drift": "project_drift",
        "unexplained_project_drift": "project_drift",
        "scope_insufficiency": "scope_insufficiency",
    }
    return aliases.get(diagnostic.code)


def worker_failure_condition(result: WorkerResult) -> str | None:
    value = result.payload.get("stop_reason")
    aliases = {
        "known_test_failure": "known_test_failure",
        "scope_insufficiency": "scope_insufficiency",
        "login_loss": "login_loss",
        "project_drift": "project_drift",
        "unexplained_project_drift": "project_drift",
    }
    return aliases.get(value) if type(value) is str else None


__all__ = [
    "AuthoritativeRevisionFinding", "AuthoritativeRevisionTask",
    "CommittedEvidence", "CommittedTerminalBundle", "EvidenceAuthority",
    "EvidenceLineageError", "ValidatedStageResult", "attempt_snapshot", "command_id",
    "evidence_snapshot", "exception_condition", "handle_matches_request",
    "handoff_snapshot", "payload_text", "review_result",
    "stage_id", "task_instruction", "terminal_command_result",
    "terminal_references", "validate_evidence_lineage", "validated_terminal_bundle",
    "validated_stage_result", "worker_failure_condition",
]

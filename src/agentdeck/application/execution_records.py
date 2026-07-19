"""Pure execution request, result, identity, and snapshot boundaries."""

from hashlib import sha256
import json
from types import MappingProxyType

from agentdeck.kernel.diagnostics import Diagnostic
from agentdeck.kernel.execution import (
    Attempt,
    Evidence,
    EvidenceKind,
    Handoff,
    ReviewFinding,
)
from agentdeck.kernel.execution_semantics import ReviewResult, validate_acceptance
from agentdeck.kernel.mission import (
    ConfirmedMissionVersion,
    MissionDraft,
    TaskDefinition,
)
from agentdeck.ports.worker import TaskRequest, WorkerHandle, WorkerResult


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


def review_finding(result: WorkerResult) -> ReviewFinding:
    evidence_ids = payload_value(result, "evidence_ids")
    if type(evidence_ids) is not tuple:
        raise ValueError("review evidence_ids must be a sequence")
    review = ReviewResult.from_mapping({
        "summary": payload_text(result, "summary"),
        "findings": [{
            "finding_id": payload_text(result, "finding_id"),
            "scope": payload_text(result, "scope"),
            "severity": payload_text(result, "severity"),
            "summary": payload_text(result, "summary"),
            "criterion": payload_text(result, "criterion"),
            "evidence_ids": list(evidence_ids),
        }],
    })
    return review.findings[0]


def typed_evidence(
    identity: str,
    task: TaskDefinition,
    result: WorkerResult,
    draft: MissionDraft,
) -> tuple[Evidence, tuple[str, ...]]:
    if task.name == "implementation":
        artifact = payload_text(result, "artifact_reference")
        return Evidence.create(identity, EvidenceKind.ARTIFACT_HASH, {
            "artifact_reference": artifact,
            "content_hash": payload_text(result, "content_hash"),
        }), (artifact,)
    if task.name == "review":
        finding = review_finding(result)
        return Evidence.create(
            identity, EvidenceKind.REVIEW_FINDING, finding.canonical_projection()
        ), ()
    if task.name == "revision":
        return Evidence.create(identity, EvidenceKind.DIFF_IDENTITY, {
            "base": payload_text(result, "base"),
            "head": payload_text(result, "head"),
            "diff_hash": payload_text(result, "diff_hash"),
        }), ()
    mapping = payload_value(result, "evidence_by_criterion")
    if not isinstance(mapping, MappingProxyType):
        raise ValueError("acceptance evidence mapping must be an object")
    acceptance = validate_acceptance(
        criteria=draft.acceptance_criteria,
        mappings=dict(mapping),
        accepted=payload_value(result, "accepted"),
        failure_reason=payload_value(result, "failure_reason"),
    )
    return Evidence.acceptance(
        identity, result=acceptance, source_kind=EvidenceKind.ACCEPTANCE_RESULT
    ), ()


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
    "attempt_snapshot", "command_id", "exception_condition",
    "handle_matches_request", "payload_text", "review_finding", "stage_id",
    "task_instruction", "typed_evidence", "worker_failure_condition",
]

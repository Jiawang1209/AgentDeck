"""Pure materialization of a validated durable execution resume snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json

from agentdeck.application.execution_records import AuthoritativeRevisionTask
from agentdeck.kernel.execution import (
    Attempt,
    AttemptState,
    Evidence,
    EvidenceKind,
    FindingSeverity,
    Handoff,
    ReviewFinding,
)
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


@dataclass(frozen=True)
class ExecutionResumePlan:
    snapshot_hash: str
    confirmed: ConfirmedMissionVersion
    draft: MissionDraft
    prior_attempts: tuple[Attempt, ...]
    prior_evidence: tuple[Evidence, ...]
    prior_handoffs: tuple[Handoff, ...]
    revision_task: AuthoritativeRevisionTask
    remaining_tasks: tuple[TaskDefinition, ...]
    first_attempt_ordinal: int

    def __post_init__(self) -> None:
        if (
            type(self.confirmed) is not ConfirmedMissionVersion
            or type(self.draft) is not MissionDraft
            or type(self.revision_task) is not AuthoritativeRevisionTask
            or type(self.prior_attempts) is not tuple
            or any(type(item) is not Attempt for item in self.prior_attempts)
            or type(self.prior_evidence) is not tuple
            or any(type(item) is not Evidence for item in self.prior_evidence)
            or type(self.prior_handoffs) is not tuple
            or any(type(item) is not Handoff for item in self.prior_handoffs)
            or type(self.remaining_tasks) is not tuple
            or any(type(item) is not TaskDefinition for item in self.remaining_tasks)
            or type(self.first_attempt_ordinal) is not int
            or self.first_attempt_ordinal < 1
        ):
            _fail()


@dataclass(frozen=True)
class _ExecutionHistory:
    attempts: tuple[Attempt, ...]
    evidence: tuple[Evidence, ...]
    handoffs: tuple[Handoff, ...]
    revision_task: AuthoritativeRevisionTask


def _fail() -> None:
    raise ExecutionResumeProjectionError(code="resume_projection_malformed")


def _canonical(value: object) -> str:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        _fail()


def _task_matches(stage: ResumeStageFacts, task: TaskDefinition, ordinal: int) -> bool:
    return (
        stage.task_id == task.task_id
        and stage.task_ordinal == ordinal
        and stage.name == task.name
        and stage.role == task.role.value
        and stage.planned_backend == task.backend
        and stage.planned_agent_instance_id == task.agent_instance_id
        and stage.acp_route == task.acp_route
        and stage.canonical_task_facts == _canonical(task.canonical_projection())
    )


def _rebuild_confirmed_mission(
    facts: ExecutionResumeFacts,
) -> tuple[MissionDraft, ConfirmedMissionVersion]:
    draft = _canonical_mission_draft(
        facts.canonical_mission_facts, facts.mission_version
    )
    confirmed = ConfirmedMissionVersion(
        facts.mission_id,
        facts.mission_version,
        facts.mission_content_hash,
        facts.canonical_mission_facts,
    )
    if len(facts.stages) != len(draft.tasks) or any(
        not _task_matches(stage, task, ordinal)
        for ordinal, (stage, task) in enumerate(
            zip(facts.stages, draft.tasks, strict=True), 1
        )
    ):
        _fail()
    return draft, confirmed


def _rebuild_attempt(
    facts: ResumeAttemptFacts, stage: ResumeStageFacts,
) -> Attempt:
    if facts.task_id != stage.task_id or facts.agent_instance_id != (
        stage.planned_agent_instance_id
    ):
        _fail()
    attempt = Attempt(
        facts.attempt_id,
        facts.task_id,
        facts.ordinal,
        AttemptState(facts.state),
        facts.reason,
        facts.result_summary,
        facts.retryable,
    )
    if attempt.state is AttemptState.INTERRUPTED and facts.effect_observed:
        _fail()
    return attempt


def _rebuild_evidence(
    facts: ResumeEvidenceFacts, stage: ResumeStageFacts,
) -> Evidence:
    if facts.task_id != stage.task_id or facts.attempt_id != stage.terminal_attempt_id:
        _fail()
    evidence = Evidence(
        facts.evidence_id,
        EvidenceKind(facts.kind),
        facts.canonical_evidence_facts,
    )
    if sha256(evidence.canonical_content.encode("utf-8")).hexdigest() != (
        facts.content_hash
    ):
        _fail()
    return evidence


def _rebuild_handoff(facts: ResumeHandoffFacts, stage: ResumeStageFacts) -> Handoff:
    try:
        payload = json.loads(facts.canonical_handoff_facts)
        handoff = Handoff(
            facts.handoff_id,
            facts.source_attempt_id,
            facts.target_task_id,
            facts.result_summary,
            tuple(payload["verification_evidence_ids"]),
            tuple(payload["artifact_references"]),
            tuple(payload["known_issues"]),
            facts.canonical_handoff_facts,
            facts.content_hash,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        _fail()
    if handoff.source_attempt_id != stage.terminal_attempt_id:
        _fail()
    return handoff


def _terminal_command_id(
    facts: ExecutionResumeFacts, task: TaskDefinition, ordinal: int,
) -> str:
    parts = (
        facts.mission_id, str(facts.mission_version), task.task_id,
        "terminal", str(ordinal),
    )
    canonical = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return "cmd_" + sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _review_finding(evidence: Evidence) -> ReviewFinding:
    try:
        payload = json.loads(evidence.canonical_content)
        return ReviewFinding(
            payload["finding_id"],
            payload["scope"],
            FindingSeverity(payload["severity"]),
            payload["summary"],
            payload["criterion"],
            tuple(payload["evidence_ids"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        _fail()


def _cross_stage_reference_ids(evidence: Evidence) -> tuple[str, ...]:
    payload = json.loads(evidence.canonical_content)
    if evidence.kind is EvidenceKind.REVIEW_FINDING:
        return tuple(payload["evidence_ids"])
    if evidence.kind is EvidenceKind.ACCEPTANCE_RESULT:
        return tuple(
            identity
            for identities in payload["evidence_by_criterion"].values()
            for identity in identities
        )
    return ()


def _rebuild_execution_history(
    snapshot: ExecutionResumeSnapshot, draft: MissionDraft,
) -> _ExecutionHistory:
    attempts: list[Attempt] = []
    evidence: list[Evidence] = []
    handoffs: list[Handoff] = []
    for index, (stage, task) in enumerate(zip(
        snapshot.facts.stages, draft.tasks, strict=True
    )):
        include_attempts = index < snapshot.closed_stage_count or (
            index == snapshot.closed_stage_count
        )
        rebuilt_attempts = tuple(
            _rebuild_attempt(item, stage) for item in stage.attempts
        )
        if tuple(item.ordinal for item in rebuilt_attempts) != tuple(
            range(1, len(rebuilt_attempts) + 1)
        ):
            _fail()
        if include_attempts:
            attempts.extend(rebuilt_attempts)
        if index < snapshot.closed_stage_count:
            if not rebuilt_attempts:
                _fail()
            rebuilt_evidence = tuple(
                _rebuild_evidence(item, stage) for item in stage.evidence
            )
            committed_ids = {item.evidence_id for item in evidence}
            for item in rebuilt_evidence:
                references = _cross_stage_reference_ids(item)
                if references and (
                    len(references) != len(set(references))
                    or not set(references) <= committed_ids
                ):
                    _fail()
            evidence.extend(rebuilt_evidence)
            rebuilt_handoff = None
            if stage.handoff is not None:
                rebuilt_handoff = _rebuild_handoff(stage.handoff, stage)
                handoffs.append(rebuilt_handoff)
            terminal = rebuilt_attempts[-1]
            next_task = (
                draft.tasks[index + 1]
                if index + 1 < len(draft.tasks) else None
            )
            if (
                terminal.state is not AttemptState.COMPLETED
                or stage.terminal_attempt_id != terminal.attempt_id
                or stage.terminal_command_id != _terminal_command_id(
                    snapshot.facts, task, terminal.ordinal
                )
                or (next_task is None) != (rebuilt_handoff is None)
                or (
                    rebuilt_handoff is not None
                    and (
                        rebuilt_handoff.target_task_id != next_task.task_id
                        or rebuilt_handoff.result_summary != terminal.result_summary
                        or rebuilt_handoff.verification_evidence_ids
                        != tuple(item.evidence_id for item in rebuilt_evidence)
                    )
                )
            ):
                _fail()
            result = {
                "mission_id": snapshot.facts.mission_id,
                "mission_version": snapshot.facts.mission_version,
                "task_id": task.task_id,
                "attempt_id": terminal.attempt_id,
                "evidence_ids": [item.evidence_id for item in rebuilt_evidence],
                "handoff_id": (
                    None if rebuilt_handoff is None else rebuilt_handoff.handoff_id
                ),
            }
            if sha256(_canonical(result).encode("utf-8")).hexdigest() != (
                stage.terminal_command_hash
            ):
                _fail()
    review_evidence = tuple(
        item for item in evidence if item.kind is EvidenceKind.REVIEW_FINDING
    )
    revision_task_id = next(
        task.task_id for task in draft.tasks if task.name == "revision"
    )
    if review_evidence:
        findings = tuple(_review_finding(item) for item in review_evidence)
        revision_task = AuthoritativeRevisionTask.from_review(
            revision_task_id, draft.scope, findings, review_evidence
        )
    else:
        revision_task = AuthoritativeRevisionTask(
            revision_task_id, "agentdeck", draft.scope, ()
        )
    return _ExecutionHistory(
        tuple(attempts), tuple(evidence), tuple(handoffs), revision_task
    )


class ExecutionResumePlanner:
    def materialize(
        self, snapshot: ExecutionResumeSnapshot
    ) -> ExecutionResumePlan:
        if type(snapshot) is not ExecutionResumeSnapshot:
            raise TypeError("resume planner requires ExecutionResumeSnapshot")
        try:
            snapshot.validate_hash()
            if snapshot.first_unclosed_task_id is None:
                raise ExecutionResumeProjectionError(
                    code="resume_mission_complete"
                )
            draft, confirmed = _rebuild_confirmed_mission(snapshot.facts)
            prior = _rebuild_execution_history(snapshot, draft)
            remaining = draft.tasks[snapshot.closed_stage_count:]
            if (
                not remaining
                or remaining[0].task_id != snapshot.first_unclosed_task_id
                or snapshot.next_attempt_ordinal is None
            ):
                _fail()
            return ExecutionResumePlan(
                snapshot_hash=snapshot.content_hash,
                confirmed=confirmed,
                draft=draft,
                prior_attempts=prior.attempts,
                prior_evidence=prior.evidence,
                prior_handoffs=prior.handoffs,
                revision_task=prior.revision_task,
                remaining_tasks=remaining,
                first_attempt_ordinal=snapshot.next_attempt_ordinal,
            )
        except ExecutionResumeProjectionError:
            raise
        except (KeyError, StopIteration, TypeError, ValueError):
            _fail()


__all__ = ["ExecutionResumePlan", "ExecutionResumePlanner"]
